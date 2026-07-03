"""Daily completeness and freshness signals for ingested health data."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, col, select

from baseline_api.db.models.enums import MetricType
from baseline_api.db.models.ingestion import (
    DailyDataQuality,
    HealthImportBatch,
    NormalizedHealthMetric,
    RawHealthSample,
)
from baseline_api.db.models.provenance import (
    NormalizedHealthMetricSourceSample,
    SleepSessionSourceSample,
)
from baseline_api.db.models.sessions import SleepSession
from baseline_api.db.repositories.ingestion import DailyDataQualityRepository
from baseline_api.observability import metrics

DEFAULT_EXPECTED_DAILY_TYPES: tuple[MetricType, ...] = (
    MetricType.heart_rate_variability,
    MetricType.sleep_duration,
)

DEFAULT_STALENESS_THRESHOLD = dt.timedelta(hours=36)


@dataclass(frozen=True, slots=True)
class FreshnessThresholds:
    """Per-metric staleness thresholds."""

    thresholds: Mapping[MetricType, dt.timedelta]

    def for_type(self, metric_type: MetricType) -> dt.timedelta:
        return self.thresholds.get(metric_type, DEFAULT_STALENESS_THRESHOLD)


class DataQualityService:
    """Compute and query daily completeness/freshness read models."""

    def __init__(
        self,
        session: Session,
        *,
        thresholds: FreshnessThresholds | None = None,
    ) -> None:
        self._session = session
        self._records = DailyDataQualityRepository(session)
        self._thresholds = thresholds or FreshnessThresholds({})

    def compute_day(
        self,
        *,
        user_id: UUID,
        day: dt.date,
        expected_types: Sequence[MetricType] = DEFAULT_EXPECTED_DAILY_TYPES,
        as_of: dt.datetime | None = None,
    ) -> DailyDataQuality:
        """Compute and persist completeness/freshness for one user-day."""

        now = as_of or dt.datetime.now(dt.UTC)
        expected = tuple(expected_types)
        present = self._present_expected_types(user_id=user_id, day=day, expected_types=expected)
        missing = [metric_type for metric_type in expected if metric_type not in present]
        ratio = len(present) / len(expected) if expected else 1.0
        freshness = self._freshness_by_type(user_id=user_id, expected_types=expected, as_of=now)
        stale_types = [
            metric_type.value
            for metric_type in expected
            if freshness[metric_type.value].get("is_stale") is True
        ]

        existing = self._records.get_by_user_day(user_id=user_id, day=day)
        payload = {
            "expected_types": [metric_type.value for metric_type in expected],
            "present_types": [
                metric_type.value for metric_type in expected if metric_type in present
            ],
            "missing_types": [metric_type.value for metric_type in missing],
            "completeness_ratio": ratio,
            "completeness_warnings": [
                _missing_warning(metric_type=metric_type, day=day) for metric_type in missing
            ],
            "freshness_by_type": freshness,
            "stale_types": stale_types,
            "computed_at": now,
        }

        if existing is None:
            record = DailyDataQuality(user_id=user_id, date=day, **payload)
            self._records.create(record)
        else:
            record = existing
            for key, value in payload.items():
                setattr(record, key, value)
            self._session.add(record)
            self._session.flush()

        metrics.set_data_completeness_by_day(ratio, day=day.isoformat())
        for metric_type in expected:
            metrics.set_data_staleness_flag(
                freshness[metric_type.value].get("is_stale") is True,
                day=day.isoformat(),
                sample_type=metric_type.value,
            )
        return record

    def compute_range(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
        expected_types: Sequence[MetricType] = DEFAULT_EXPECTED_DAILY_TYPES,
        as_of: dt.datetime | None = None,
    ) -> list[DailyDataQuality]:
        """Compute quality rows for ``[start_date, end_date)``."""

        records: list[DailyDataQuality] = []
        current = start_date
        while current < end_date:
            records.append(
                self.compute_day(
                    user_id=user_id,
                    day=current,
                    expected_types=expected_types,
                    as_of=as_of,
                )
            )
            current += dt.timedelta(days=1)
        return records

    def get_daily_quality(self, *, user_id: UUID, day: dt.date) -> DailyDataQuality | None:
        return self._records.get_by_user_day(user_id=user_id, day=day)

    def _present_expected_types(
        self,
        *,
        user_id: UUID,
        day: dt.date,
        expected_types: Sequence[MetricType],
    ) -> set[MetricType]:
        return {
            metric_type
            for metric_type in expected_types
            if self._has_usable_output_for_day(
                user_id=user_id,
                metric_type=metric_type,
                day=day,
            )
        }

    def _has_usable_output_for_day(
        self,
        *,
        user_id: UUID,
        metric_type: MetricType,
        day: dt.date,
    ) -> bool:
        start_at, end_at = _day_bounds(day)
        if metric_type == MetricType.sleep_duration:
            return (
                self._session.exec(
                    select(SleepSession.id)
                    .where(
                        SleepSession.user_id == user_id,
                        SleepSession.start_time >= start_at,
                        SleepSession.start_time < end_at,
                    )
                    .limit(1)
                ).first()
                is not None
            )

        return (
            self._session.exec(
                select(NormalizedHealthMetric.id)
                .where(
                    NormalizedHealthMetric.user_id == user_id,
                    NormalizedHealthMetric.metric_type == metric_type,
                    NormalizedHealthMetric.start_time >= start_at,
                    NormalizedHealthMetric.start_time < end_at,
                )
                .limit(1)
            ).first()
            is not None
        )

    def _freshness_by_type(
        self,
        *,
        user_id: UUID,
        expected_types: Sequence[MetricType],
        as_of: dt.datetime,
    ) -> dict[str, dict[str, object]]:
        freshness: dict[str, dict[str, object]] = {}
        for metric_type in expected_types:
            latest = self._latest_successful_sync_at(
                user_id=user_id,
                metric_type=metric_type,
                as_of=as_of,
            )
            threshold = self._thresholds.for_type(metric_type)
            if latest is None:
                freshness[metric_type.value] = {
                    "last_successful_sync_at": None,
                    "age_seconds": None,
                    "threshold_seconds": threshold.total_seconds(),
                    "is_stale": True,
                    "reason": "no_successful_sync",
                }
                continue

            latest_at = _as_utc_aware(latest)
            age = _as_utc_aware(as_of) - latest_at
            is_stale = age > threshold
            payload: dict[str, object] = {
                "last_successful_sync_at": latest_at.isoformat(),
                "age_seconds": age.total_seconds(),
                "threshold_seconds": threshold.total_seconds(),
                "is_stale": is_stale,
            }
            if is_stale:
                payload["reason"] = "last_successful_sync_exceeded_threshold"
            freshness[metric_type.value] = payload
        return freshness

    def _latest_successful_sync_at(
        self,
        *,
        user_id: UUID,
        metric_type: MetricType,
        as_of: dt.datetime,
    ) -> dt.datetime | None:
        if metric_type == MetricType.sleep_duration:
            statement = (
                select(HealthImportBatch.imported_at)
                .select_from(SleepSession)
                .join(
                    SleepSessionSourceSample,
                    col(SleepSession.id) == col(SleepSessionSourceSample.sleep_session_id),
                )
                .join(
                    RawHealthSample,
                    col(SleepSessionSourceSample.raw_health_sample_id) == col(RawHealthSample.id),
                )
                .join(
                    HealthImportBatch,
                    col(RawHealthSample.import_batch_id) == col(HealthImportBatch.id),
                )
                .where(
                    SleepSession.user_id == user_id,
                    RawHealthSample.user_id == user_id,
                    HealthImportBatch.user_id == user_id,
                    HealthImportBatch.imported_at <= as_of,
                )
                .order_by(col(HealthImportBatch.imported_at).desc())
                .limit(1)
            )
        else:
            statement = (
                select(HealthImportBatch.imported_at)
                .select_from(NormalizedHealthMetric)
                .join(
                    NormalizedHealthMetricSourceSample,
                    col(NormalizedHealthMetric.id)
                    == col(NormalizedHealthMetricSourceSample.normalized_health_metric_id),
                )
                .join(
                    RawHealthSample,
                    col(NormalizedHealthMetricSourceSample.raw_health_sample_id)
                    == col(RawHealthSample.id),
                )
                .join(
                    HealthImportBatch,
                    col(RawHealthSample.import_batch_id) == col(HealthImportBatch.id),
                )
                .where(
                    NormalizedHealthMetric.user_id == user_id,
                    NormalizedHealthMetric.metric_type == metric_type,
                    RawHealthSample.user_id == user_id,
                    HealthImportBatch.user_id == user_id,
                    HealthImportBatch.imported_at <= as_of,
                )
                .order_by(col(HealthImportBatch.imported_at).desc())
                .limit(1)
            )
        return self._session.exec(statement).first()


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start_at = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
    return start_at, start_at + dt.timedelta(days=1)


def _as_utc_aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _missing_warning(*, metric_type: MetricType, day: dt.date) -> dict[str, str]:
    return {
        "code": "missing_expected_type",
        "severity": "warning",
        "sample_type": metric_type.value,
        "message": f"Missing expected {metric_type.value} data for {day.isoformat()}.",
    }
