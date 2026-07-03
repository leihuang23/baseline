"""arq worker function for the daily feature-analysis pipeline."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, col, select

from baseline_api.config import get_settings
from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.enums import MetricType
from baseline_api.db.models.features import DerivedDailyFeature
from baseline_api.db.models.ingestion import NormalizedHealthMetric
from baseline_api.db.models.sessions import SleepSession, WorkoutSession
from baseline_api.db.models.user import User
from baseline_api.features.assembler import assemble_daily_features
from baseline_api.features.cardio import CardioSampleInput
from baseline_api.features.sleep import SleepSessionInput
from baseline_api.features.training_load import VO2SampleInput, WorkoutSessionInput


def _window_start(target_date: dt.date, days: int) -> dt.datetime:
    return dt.datetime.combine(
        target_date - dt.timedelta(days=days - 1),
        dt.time.min,
        tzinfo=dt.UTC,
    )


def _target_end(target_date: dt.date) -> dt.datetime:
    return dt.datetime.combine(target_date, dt.time.max, tzinfo=dt.UTC)


async def daily_analysis(
    ctx: dict[str, Any],
    checkin_id: str,
    user_id: str,
    date_str: str,
) -> dict[str, Any]:
    """arq job entrypoint: assemble and persist daily features for one check-in."""

    session_maker: sessionmaker[Session] = ctx["session_maker"]
    target_date = dt.date.fromisoformat(date_str)
    user_uuid = UUID(user_id)

    with session_maker() as session:
        checkin = session.get(DailyCheckIn, UUID(checkin_id))
        if checkin is None:
            return {
                "status": "error",
                "error": "checkin_not_found",
                "checkin_id": checkin_id,
            }

        user = session.get(User, user_uuid)
        if user is None:
            return {
                "status": "error",
                "error": "user_not_found",
                "user_id": user_id,
            }

        sleep_sessions = _load_sleep_sessions(session, user_uuid, target_date)
        hrv_samples = _load_cardio_samples(
            session, user_uuid, target_date, MetricType.heart_rate_variability
        )
        rhr_samples = _load_cardio_samples(
            session, user_uuid, target_date, MetricType.resting_heart_rate
        )
        workouts = _load_workouts(session, user_uuid, target_date)
        vo2_samples = _load_vo2_samples(session, user_uuid, target_date)

        bundle = assemble_daily_features(
            target_date,
            sleep_sessions=sleep_sessions,
            hrv_samples=hrv_samples,
            rhr_samples=rhr_samples,
            workouts=workouts,
            vo2_samples=vo2_samples,
            personal_sleep_need_hours=8.0,
            computed_at=dt.datetime.now(dt.UTC),
        )

        feature = _upsert_derived_daily_feature(
            session,
            user_uuid,
            target_date,
            bundle.to_derived_daily_feature_fields(),
        )
        session.flush()
        feature_id = str(feature.id)
        session.commit()

    return {
        "status": "success",
        "derived_daily_feature_id": feature_id,
        "feature_version": bundle.feature_version,
        "date": target_date.isoformat(),
        "user_id": user_id,
        "checkin_id": checkin_id,
    }


def _load_sleep_sessions(
    session: Session,
    user_id: UUID,
    target_date: dt.date,
) -> list[SleepSessionInput]:
    window_start = _window_start(target_date, 7)
    target_end = _target_end(target_date)
    rows = session.exec(
        select(SleepSession)
        .where(SleepSession.user_id == user_id)
        .where(col(SleepSession.end_time).is_not(None))
        .where(col(SleepSession.end_time) >= window_start)
        .where(col(SleepSession.end_time) <= target_end)
        .order_by(col(SleepSession.end_time))
    ).all()

    return [
        SleepSessionInput(
            start_time=row.start_time,
            end_time=row.end_time,
            duration_seconds=row.duration,
            sleep_stage_breakdown=row.sleep_stage_breakdown or {},
            interruptions=row.interruptions,
            quality_proxy=row.quality_proxy,
            confidence=row.confidence,
            source_sample_ids=tuple(row.source_sample_ids),
        )
        for row in rows
    ]


def _load_cardio_samples(
    session: Session,
    user_id: UUID,
    target_date: dt.date,
    metric_type: MetricType,
) -> list[CardioSampleInput]:
    window_start = _window_start(target_date, 28)
    target_end = _target_end(target_date)
    rows = session.exec(
        select(NormalizedHealthMetric)
        .where(NormalizedHealthMetric.user_id == user_id)
        .where(NormalizedHealthMetric.metric_type == metric_type)
        .where(NormalizedHealthMetric.start_time >= window_start)
        .where(NormalizedHealthMetric.start_time <= target_end)
        .order_by(col(NormalizedHealthMetric.start_time))
    ).all()

    return [
        CardioSampleInput(
            sample_id=str(row.id),
            start_time=row.start_time,
            value=row.value,
            source_sample_ids=tuple(row.source_sample_ids),
            confidence=row.confidence,
        )
        for row in rows
    ]


def _load_workouts(
    session: Session,
    user_id: UUID,
    target_date: dt.date,
) -> list[WorkoutSessionInput]:
    window_start = _window_start(target_date, 28)
    target_end = _target_end(target_date)
    rows = session.exec(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user_id)
        .where(WorkoutSession.start_time >= window_start)
        .where(WorkoutSession.start_time <= target_end)
        .order_by(col(WorkoutSession.start_time))
    ).all()

    return [
        WorkoutSessionInput(
            session_id=str(row.id),
            start_time=row.start_time,
            end_time=row.end_time,
            modality=row.modality.value,
            duration_seconds=row.duration,
            distance_meters=row.distance,
            active_energy_kcal=row.active_energy,
            average_hr_bpm=row.average_hr,
            max_hr_bpm=row.max_hr,
            intensity_zone_distribution=row.intensity_zone_distribution or {},
            perceived_exertion=row.perceived_exertion,
            muscle_group_tags=row.muscle_group_tags or [],
            confidence=row.confidence,
            source_sample_ids=tuple(row.source_sample_ids),
        )
        for row in rows
    ]


def _load_vo2_samples(
    session: Session,
    user_id: UUID,
    target_date: dt.date,
) -> list[VO2SampleInput]:
    window_start = _window_start(target_date, 28)
    target_end = _target_end(target_date)
    rows = session.exec(
        select(NormalizedHealthMetric)
        .where(NormalizedHealthMetric.user_id == user_id)
        .where(NormalizedHealthMetric.metric_type == MetricType.vo2_max)
        .where(NormalizedHealthMetric.start_time >= window_start)
        .where(NormalizedHealthMetric.start_time <= target_end)
        .order_by(col(NormalizedHealthMetric.start_time))
    ).all()

    return [
        VO2SampleInput(
            sample_id=str(row.id),
            start_time=row.start_time,
            value=row.value,
            source_sample_ids=tuple(row.source_sample_ids),
        )
        for row in rows
    ]


def _upsert_derived_daily_feature(
    session: Session,
    user_id: UUID,
    target_date: dt.date,
    fields: dict[str, Any],
) -> DerivedDailyFeature:
    statement = insert(DerivedDailyFeature).values(
        user_id=user_id,
        date=target_date,
        **fields,
    )
    update_fields: dict[str, Any] = {key: statement.excluded[key] for key in fields}
    update_fields["updated_at"] = dt.datetime.now(dt.UTC)
    upsert_statement = statement.on_conflict_do_update(
        constraint="uq_derived_daily_feature_user_date",
        set_=update_fields,
    ).returning(col(DerivedDailyFeature.id))

    feature_id = cast(UUID, session.execute(upsert_statement).scalar_one())
    feature = session.get(DerivedDailyFeature, feature_id)
    if feature is None:
        raise RuntimeError("derived_daily_feature_upsert_failed")
    return feature


async def on_startup(ctx: dict[str, Any]) -> None:
    """Create a SQLAlchemy engine bound to the session maker."""

    settings = get_settings()
    engine = create_engine(str(settings.database_url))
    ctx["engine"] = engine
    ctx["session_maker"] = sessionmaker(bind=engine, class_=Session)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    """Dispose the SQLAlchemy engine on worker shutdown."""

    engine = ctx.get("engine")
    if engine is not None:
        engine.dispose()
