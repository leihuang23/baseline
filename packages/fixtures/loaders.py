"""Loader utilities for fixture datasets."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlmodel import Session

from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.enums import MetricType, Modality, PrivacyMode, SensitiveNotePolicy
from baseline_api.db.models.ingestion import NormalizedHealthMetric, RawHealthSample
from baseline_api.db.models.sessions import SleepSession, WorkoutSession
from baseline_api.db.models.user import User
from packages.fixtures.models import FixtureDataset


@dataclass(slots=True)
class LoadedFixture:
    """Database objects created for a fixture load."""

    user: User
    raw_sample_count: int
    normalized_metric_count: int
    workout_count: int
    sleep_count: int
    checkin_count: int


def load_fixture(
    session: Session,
    dataset: FixtureDataset,
    *,
    user: User | None = None,
) -> LoadedFixture:
    """Insert a synthetic fixture into the test database."""

    fixture_user = user or User(
        id=_fixture_uuid(dataset.name, "user"),
        timezone=dataset.timezone,
        locale="en",
        privacy_mode=PrivacyMode.local_only,
        active_consent_version="synthetic-v1",
    )
    if user is None:
        session.add(fixture_user)
        session.flush()

    import_batch_id = _fixture_uuid(dataset.name, dataset.seed, "import-batch")
    imported_at = dt.datetime.combine(dataset.start_date, dt.time(0, 0), tzinfo=dt.UTC)

    for sample in dataset.samples:
        metric_type = MetricType(sample.metric_type)
        session.add(
            RawHealthSample(
                id=_fixture_uuid(sample.sample_id, "raw"),
                user_id=fixture_user.id,
                source_platform="apple_health_synthetic",
                source_device="Baseline Synthetic Watch",
                source_sample_id=sample.sample_id,
                sample_type=metric_type,
                start_time=sample.start_time,
                end_time=sample.end_time,
                raw_value=sample.value,
                raw_unit=sample.unit,
                source_metadata=sample.metadata,
                imported_at=imported_at,
                import_batch_id=import_batch_id,
            )
        )
        session.add(
            NormalizedHealthMetric(
                id=_fixture_uuid(sample.sample_id, "normalized"),
                user_id=fixture_user.id,
                metric_type=metric_type,
                start_time=sample.start_time,
                end_time=sample.end_time,
                value=sample.value,
                unit=sample.unit,
                confidence=1.0,
                source_sample_ids=[sample.sample_id],
                normalization_version="synthetic-v1",
            )
        )

    for workout in dataset.workouts:
        session.add(
            WorkoutSession(
                id=_fixture_uuid(workout.workout_id, "workout"),
                user_id=fixture_user.id,
                start_time=workout.start_time,
                end_time=workout.end_time,
                modality=Modality(workout.modality),
                distance=workout.distance_meters,
                duration=workout.duration_seconds,
                active_energy=workout.active_energy_kcal,
                average_hr=workout.average_hr_bpm,
                max_hr=workout.max_hr_bpm,
                intensity_zone_distribution=workout.intensity_zone_distribution,
                perceived_exertion=workout.perceived_exertion,
                muscle_group_tags=workout.muscle_group_tags,
                source_sample_ids=workout.source_sample_ids,
            )
        )

    for sleep in dataset.sleep_sessions:
        session.add(
            SleepSession(
                id=_fixture_uuid(sleep.sleep_id, "sleep"),
                user_id=fixture_user.id,
                start_time=sleep.start_time,
                end_time=sleep.end_time,
                duration=sleep.duration_seconds,
                sleep_stage_breakdown=sleep.stage_seconds,
                interruptions=sleep.interruptions,
                quality_proxy=sleep.quality_proxy,
                source_sample_ids=sleep.source_sample_ids,
            )
        )

    for checkin in dataset.checkins:
        session.add(
            DailyCheckIn(
                id=_fixture_uuid(checkin.checkin_id, "checkin"),
                user_id=fixture_user.id,
                date=checkin.date,
                energy_score=checkin.energy_score,
                mood_score=checkin.mood_score,
                soreness_score=checkin.soreness_score,
                stress_score=checkin.stress_score,
                perceived_recovery_score=checkin.perceived_recovery_score,
                food_quality_score=checkin.food_quality_score,
                alcohol_flag=checkin.alcohol_flag,
                caffeine_notes=checkin.caffeine_notes,
                illness_flag=checkin.illness_flag,
                injury_flag=checkin.injury_flag,
                travel_flag=checkin.travel_flag,
                sensitive_note_policy=SensitiveNotePolicy.exclude_from_external_llm,
                structured_notes=checkin.structured_notes,
                free_text_note_reference=checkin.free_text_note_reference,
            )
        )

    session.flush()
    return LoadedFixture(
        user=fixture_user,
        raw_sample_count=len(dataset.samples),
        normalized_metric_count=len(dataset.samples),
        workout_count=len(dataset.workouts),
        sleep_count=len(dataset.sleep_sessions),
        checkin_count=len(dataset.checkins),
    )


def emit_raw_sync_payload(dataset: FixtureDataset) -> dict[str, Any]:
    """Emit a HealthKit-like raw-sync payload for API contract tests."""

    return {
        "source_platform": "apple_health_synthetic",
        "source_device": "Baseline Synthetic Watch",
        "sync_anchor": f"synthetic:{dataset.name}:{dataset.seed}:{dataset.days}",
        "samples": [
            {
                "source_sample_id": sample.sample_id,
                "sample_type": sample.metric_type,
                "start_time": sample.start_time.isoformat(),
                "end_time": sample.end_time.isoformat() if sample.end_time else None,
                "raw_value": sample.value,
                "raw_unit": sample.unit,
                "source_metadata": sample.metadata,
            }
            for sample in dataset.samples
        ],
        "workouts": [
            {
                "source_sample_id": workout.workout_id,
                "start_time": workout.start_time.isoformat(),
                "end_time": workout.end_time.isoformat(),
                "modality": workout.modality,
                "distance_meters": workout.distance_meters,
                "duration_seconds": workout.duration_seconds,
                "active_energy_kcal": workout.active_energy_kcal,
                "average_hr_bpm": workout.average_hr_bpm,
                "max_hr_bpm": workout.max_hr_bpm,
                "intensity_zone_distribution": workout.intensity_zone_distribution,
                "perceived_exertion": workout.perceived_exertion,
                "muscle_group_tags": workout.muscle_group_tags,
            }
            for workout in dataset.workouts
        ],
        "sleep_sessions": [
            {
                "source_sample_id": sleep.sleep_id,
                "start_time": sleep.start_time.isoformat(),
                "end_time": sleep.end_time.isoformat(),
                "duration_seconds": sleep.duration_seconds,
                "stage_seconds": sleep.stage_seconds,
                "interruptions": sleep.interruptions,
                "quality_proxy": sleep.quality_proxy,
            }
            for sleep in dataset.sleep_sessions
        ],
    }


def _fixture_uuid(*parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, ":".join(str(part) for part in parts))
