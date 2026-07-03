"""Tests for P1-03 daily data quality and freshness read model."""

from __future__ import annotations

import datetime as dt
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, select

from baseline_api.db.models.enums import MetricType, PrivacyMode
from baseline_api.db.models.ingestion import DailyDataQuality, HealthImportBatch, RawHealthSample
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.ingestion.data_quality import (
    DataQualityService,
    FreshnessThresholds,
)
from baseline_api.ingestion.normalization import NormalizationService


def _seed_user(session: Session) -> User:
    user = User(privacy_mode=PrivacyMode.local_only, active_consent_version="v1")
    session.add(user)
    session.flush()
    session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=["all"],
            timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        )
    )
    session.flush()
    return user


def _batch(
    session: Session,
    user_id: UUID,
    *,
    imported_at: dt.datetime | None = None,
) -> HealthImportBatch:
    batch = HealthImportBatch(
        user_id=user_id,
        client_sync_id=f"dq-{uuid4()}",
        request_hash="test-hash",
        source_platform="apple_health",
        source_device="test-watch",
        timezone="UTC",
        next_anchor="test-anchor",
        accepted_count=0,
        duplicate_count=0,
        rejected_count=0,
        imported_at=imported_at or dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.UTC),
    )
    session.add(batch)
    session.flush()
    return batch


def _raw(
    session: Session,
    batch: HealthImportBatch,
    *,
    sample_type: MetricType,
    start_time: dt.datetime,
    source_sample_id: str,
    raw_unit: str | None = None,
    raw_value: float = 50.0,
) -> RawHealthSample:
    raw = RawHealthSample(
        user_id=batch.user_id,
        source_platform="apple_health",
        source_device="test-watch",
        source_sample_id=source_sample_id,
        content_hash=source_sample_id,
        sample_type=sample_type,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=5),
        raw_value=raw_value,
        raw_unit=raw_unit or ("ms" if sample_type == MetricType.heart_rate_variability else "h"),
        source_metadata={"synthetic": True},
        imported_at=batch.imported_at,
        import_batch_id=batch.id,
    )
    session.add(raw)
    session.flush()
    return raw


def _normalize(session: Session, batch: HealthImportBatch) -> None:
    NormalizationService(session).normalize_batch(import_batch_id=batch.id, user_id=batch.user_id)


def test_missing_hrv_produces_completeness_warning(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _batch(db_session, user.id)
    day = dt.date(2026, 1, 15)
    _raw(
        db_session,
        batch,
        sample_type=MetricType.sleep_duration,
        start_time=dt.datetime(2026, 1, 15, 22, 0, tzinfo=dt.UTC),
        source_sample_id="sleep-present",
    )
    _normalize(db_session, batch)

    record = DataQualityService(db_session).compute_day(user_id=user.id, day=day)

    assert record.date == day
    assert record.completeness_ratio == pytest.approx(0.5)
    assert record.present_types == [MetricType.sleep_duration.value]
    assert record.missing_types == [MetricType.heart_rate_variability.value]
    assert record.completeness_warnings == [
        {
            "code": "missing_expected_type",
            "severity": "warning",
            "sample_type": MetricType.heart_rate_variability.value,
            "message": "Missing expected heart_rate_variability data for 2026-01-15.",
        }
    ]


def test_missing_sleep_produces_completeness_warning(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _batch(db_session, user.id)
    day = dt.date(2026, 1, 15)
    _raw(
        db_session,
        batch,
        sample_type=MetricType.heart_rate_variability,
        start_time=dt.datetime(2026, 1, 15, 7, 0, tzinfo=dt.UTC),
        source_sample_id="hrv-present",
    )
    _normalize(db_session, batch)

    record = DataQualityService(db_session).compute_day(user_id=user.id, day=day)

    assert record.completeness_ratio == pytest.approx(0.5)
    assert record.present_types == [MetricType.heart_rate_variability.value]
    assert record.missing_types == [MetricType.sleep_duration.value]
    assert record.completeness_warnings[0]["sample_type"] == MetricType.sleep_duration.value


def test_complete_day_has_no_completeness_warning(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _batch(db_session, user.id)
    day = dt.date(2026, 1, 15)
    _raw(
        db_session,
        batch,
        sample_type=MetricType.heart_rate_variability,
        start_time=dt.datetime(2026, 1, 15, 7, 0, tzinfo=dt.UTC),
        source_sample_id="hrv-present",
    )
    _raw(
        db_session,
        batch,
        sample_type=MetricType.sleep_duration,
        start_time=dt.datetime(2026, 1, 15, 22, 0, tzinfo=dt.UTC),
        source_sample_id="sleep-present",
    )
    _normalize(db_session, batch)

    record = DataQualityService(db_session).compute_day(user_id=user.id, day=day)

    assert record.completeness_ratio == pytest.approx(1.0)
    assert record.missing_types == []
    assert record.completeness_warnings == []


def test_rejected_raw_sample_is_not_counted_complete_or_fresh(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _batch(
        db_session,
        user.id,
        imported_at=dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.UTC),
    )
    day = dt.date(2026, 1, 15)
    _raw(
        db_session,
        batch,
        sample_type=MetricType.heart_rate_variability,
        start_time=dt.datetime(2026, 1, 15, 7, 0, tzinfo=dt.UTC),
        source_sample_id="unusable-hrv",
        raw_unit="unsupported-unit",
    )
    _normalize(db_session, batch)

    record = DataQualityService(db_session).compute_day(
        user_id=user.id,
        day=day,
        expected_types=(MetricType.heart_rate_variability,),
        as_of=dt.datetime(2026, 1, 15, 10, 0, tzinfo=dt.UTC),
    )

    assert record.completeness_ratio == pytest.approx(0.0)
    assert record.present_types == []
    assert record.missing_types == [MetricType.heart_rate_variability.value]
    freshness = record.freshness_by_type[MetricType.heart_rate_variability.value]
    assert freshness["is_stale"] is True
    assert freshness["reason"] == "no_successful_sync"
    assert freshness["last_successful_sync_at"] is None


def test_stale_data_includes_reason_and_age_while_fresh_data_does_not(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _seed_user(db_session)
    stale_batch = _batch(
        db_session,
        user.id,
        imported_at=dt.datetime(2026, 1, 16, 7, 5, tzinfo=dt.UTC),
    )
    fresh_batch = _batch(
        db_session,
        user.id,
        imported_at=dt.datetime(2026, 1, 17, 7, 30, tzinfo=dt.UTC),
    )
    _raw(
        db_session,
        stale_batch,
        sample_type=MetricType.heart_rate_variability,
        start_time=dt.datetime(2026, 1, 10, 7, 0, tzinfo=dt.UTC),
        source_sample_id="old-hrv",
    )
    _normalize(db_session, stale_batch)
    _raw(
        db_session,
        fresh_batch,
        sample_type=MetricType.sleep_duration,
        start_time=dt.datetime(2026, 1, 10, 22, 0, tzinfo=dt.UTC),
        source_sample_id="fresh-sleep",
    )
    _normalize(db_session, fresh_batch)
    thresholds = FreshnessThresholds(
        {
            MetricType.heart_rate_variability: dt.timedelta(hours=24),
            MetricType.sleep_duration: dt.timedelta(hours=24),
        }
    )
    staleness_metrics: list[tuple[bool, str, str]] = []

    def set_data_staleness_flag(is_stale: bool, *, day: str, sample_type: str) -> None:
        staleness_metrics.append((is_stale, day, sample_type))

    monkeypatch.setattr(
        "baseline_api.ingestion.data_quality.metrics.set_data_staleness_flag",
        set_data_staleness_flag,
    )

    record = DataQualityService(db_session, thresholds=thresholds).compute_day(
        user_id=user.id,
        day=dt.date(2026, 1, 17),
        as_of=dt.datetime(2026, 1, 17, 8, 0, tzinfo=dt.UTC),
    )

    stale_hrv = record.freshness_by_type[MetricType.heart_rate_variability.value]
    fresh_sleep = record.freshness_by_type[MetricType.sleep_duration.value]
    assert stale_hrv["is_stale"] is True
    assert stale_hrv["reason"] == "last_successful_sync_exceeded_threshold"
    assert stale_hrv["last_successful_sync_at"] == "2026-01-16T07:05:00+00:00"
    assert stale_hrv["age_seconds"] == pytest.approx(89700.0)
    assert fresh_sleep["is_stale"] is False
    assert fresh_sleep["last_successful_sync_at"] == "2026-01-17T07:30:00+00:00"
    assert "reason" not in fresh_sleep
    assert record.stale_types == [MetricType.heart_rate_variability.value]
    assert staleness_metrics == [
        (True, "2026-01-17", MetricType.heart_rate_variability.value),
        (False, "2026-01-17", MetricType.sleep_duration.value),
    ]


def test_quality_read_model_is_queryable_per_user_per_day(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _batch(db_session, user.id)
    day = dt.date(2026, 1, 15)
    _raw(
        db_session,
        batch,
        sample_type=MetricType.heart_rate_variability,
        start_time=dt.datetime(2026, 1, 15, 7, 0, tzinfo=dt.UTC),
        source_sample_id="query-hrv",
    )
    _normalize(db_session, batch)

    service = DataQualityService(db_session)
    written = service.compute_day(user_id=user.id, day=day)

    queried = service.get_daily_quality(user_id=user.id, day=day)

    assert queried is not None
    assert queried.id == written.id
    assert queried.user_id == user.id
    assert queried.date == day
    assert db_session.exec(select(DailyDataQuality)).one().id == written.id
