"""Core normalization service: raw samples → canonical records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlmodel import Session, col, select

from baseline_api.db.models.enums import MetricType, Modality
from baseline_api.db.models.ingestion import (
    HealthImportBatch,
    NormalizedHealthMetric,
    RawHealthSample,
)
from baseline_api.db.models.provenance import (
    NormalizedHealthMetricSourceSample,
    SleepSessionSourceSample,
    WorkoutSessionSourceSample,
)
from baseline_api.db.models.sessions import SleepSession, WorkoutSession
from baseline_api.ingestion.normalization.conflicts import (
    SleepCandidate,
    WorkoutCandidate,
    resolve_session_conflicts,
)
from baseline_api.ingestion.normalization.units import (
    canonical_value_from_metadata,
    normalize_value,
)

NORMALIZATION_VERSION = "p1-02-v1"


@dataclass(slots=True)
class NormalizationResult:
    """Outcome of normalizing a single import batch."""

    import_batch_id: UUID
    normalized_metric_count: int
    workout_count: int
    sleep_count: int
    warnings: list[str] = field(default_factory=list)

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        return {
            "import_batch_id": str(self.import_batch_id),
            "normalized_metric_count": self.normalized_metric_count,
            "workout_count": self.workout_count,
            "sleep_count": self.sleep_count,
            "warnings": self.warnings,
        }


class NormalizationService:
    """Transform raw HealthKit samples into canonical metrics and sessions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def normalize_batch(
        self,
        *,
        import_batch_id: UUID,
        user_id: UUID,
    ) -> NormalizationResult:
        """Normalize all raw samples for a batch, idempotently.

        Re-running the same batch deletes previously normalized rows linked to
        its raw samples and rebuilds them, so no duplicates are produced.
        """

        batch = self._session.get(HealthImportBatch, import_batch_id)
        if batch is None:
            raise ValueError(f"Import batch {import_batch_id} not found")

        raw_samples = list(
            self._session.exec(
                select(RawHealthSample).where(
                    RawHealthSample.import_batch_id == import_batch_id,
                    RawHealthSample.user_id == user_id,
                )
            ).all()
        )

        self._delete_existing_normalized_rows(raw_samples)

        normalized_metrics: list[NormalizedHealthMetric] = []
        workout_candidates: list[WorkoutCandidate] = []
        sleep_candidates: list[SleepCandidate] = []
        warnings: list[str] = []

        for raw in raw_samples:
            if raw.sample_type == MetricType.workout:
                workout_candidate, rejection = _build_workout_candidate(raw)
                if workout_candidate is not None:
                    workout_candidates.append(workout_candidate)
                else:
                    warnings.append(f"Rejected workout sample {raw.source_sample_id}: {rejection}")
            elif raw.sample_type == MetricType.sleep_duration:
                sleep_candidate, rejection = _build_sleep_candidate(raw)
                if sleep_candidate is not None:
                    sleep_candidates.append(sleep_candidate)
                else:
                    warnings.append(f"Rejected sleep sample {raw.source_sample_id}: {rejection}")
            else:
                metric = _build_scalar_metric(raw)
                if metric is not None:
                    if metric.confidence == 0.0:
                        warnings.append(
                            f"Rejected normalized metric for {raw.sample_type.value} "
                            f"sample {raw.source_sample_id}: unit {raw.raw_unit!r} unknown"
                        )
                    else:
                        normalized_metrics.append(metric)

        resolved_workouts, resolved_sleep, conflict_warnings = resolve_session_conflicts(
            workout_candidates,
            sleep_candidates,
        )
        warnings.extend(conflict_warnings)

        workout_sessions = [_workout_from_candidate(c, user_id) for c in resolved_workouts]
        sleep_sessions = [_sleep_from_candidate(c, user_id) for c in resolved_sleep]

        for metric in normalized_metrics:
            self._session.add(metric)
        for workout in workout_sessions:
            self._session.add(workout)
        for sleep in sleep_sessions:
            self._session.add(sleep)

        self._session.flush()
        self._link_provenance(normalized_metrics, workout_sessions, sleep_sessions)

        return NormalizationResult(
            import_batch_id=import_batch_id,
            normalized_metric_count=len(normalized_metrics),
            workout_count=len(workout_sessions),
            sleep_count=len(sleep_sessions),
            warnings=warnings,
        )

    def _delete_existing_normalized_rows(
        self,
        raw_samples: list[RawHealthSample],
    ) -> None:
        """Remove prior normalized output linked to these raw samples."""

        if not raw_samples:
            return

        raw_ids = [raw.id for raw in raw_samples]

        metric_links = self._session.exec(
            select(NormalizedHealthMetricSourceSample).where(
                col(NormalizedHealthMetricSourceSample.raw_health_sample_id).in_(raw_ids)
            )
        ).all()
        workout_links = self._session.exec(
            select(WorkoutSessionSourceSample).where(
                col(WorkoutSessionSourceSample.raw_health_sample_id).in_(raw_ids)
            )
        ).all()
        sleep_links = self._session.exec(
            select(SleepSessionSourceSample).where(
                col(SleepSessionSourceSample.raw_health_sample_id).in_(raw_ids)
            )
        ).all()

        metric_ids = [link.normalized_health_metric_id for link in metric_links]
        workout_ids = [link.workout_session_id for link in workout_links]
        sleep_ids = [link.sleep_session_id for link in sleep_links]

        if metric_ids:
            self._session.exec(
                delete(NormalizedHealthMetricSourceSample).where(
                    col(NormalizedHealthMetricSourceSample.normalized_health_metric_id).in_(
                        metric_ids
                    )
                )
            )
            self._session.exec(
                delete(NormalizedHealthMetric).where(col(NormalizedHealthMetric.id).in_(metric_ids))
            )

        if workout_ids:
            self._session.exec(
                delete(WorkoutSessionSourceSample).where(
                    col(WorkoutSessionSourceSample.workout_session_id).in_(workout_ids)
                )
            )
            self._session.exec(
                delete(WorkoutSession).where(col(WorkoutSession.id).in_(workout_ids))
            )

        if sleep_ids:
            self._session.exec(
                delete(SleepSessionSourceSample).where(
                    col(SleepSessionSourceSample.sleep_session_id).in_(sleep_ids)
                )
            )
            self._session.exec(delete(SleepSession).where(col(SleepSession.id).in_(sleep_ids)))

    def _link_provenance(
        self,
        metrics: list[NormalizedHealthMetric],
        workouts: list[WorkoutSession],
        sleep_sessions: list[SleepSession],
    ) -> None:
        """Create link-table rows mapping canonical records to raw samples.

        Each canonical record carries the originating raw sample id in a
        temporary ``_raw_health_sample_id`` attribute. This is required because
        P1-01 dedupes raw samples by ``source_sample_id + content_hash``, so
        multiple rows may share the same ``source_sample_id`` and a
        source-id-only lookup would link them to the wrong raw row.
        """

        links: list[
            NormalizedHealthMetricSourceSample
            | WorkoutSessionSourceSample
            | SleepSessionSourceSample
        ] = []

        for metric in metrics:
            raw_id = getattr(metric, "_raw_health_sample_id", None)
            if isinstance(raw_id, UUID):
                links.append(
                    NormalizedHealthMetricSourceSample(
                        normalized_health_metric_id=metric.id,
                        raw_health_sample_id=raw_id,
                    )
                )

        for workout in workouts:
            raw_id = getattr(workout, "_raw_health_sample_id", None)
            if isinstance(raw_id, UUID):
                links.append(
                    WorkoutSessionSourceSample(
                        workout_session_id=workout.id,
                        raw_health_sample_id=raw_id,
                    )
                )

        for sleep in sleep_sessions:
            raw_id = getattr(sleep, "_raw_health_sample_id", None)
            if isinstance(raw_id, UUID):
                links.append(
                    SleepSessionSourceSample(
                        sleep_session_id=sleep.id,
                        raw_health_sample_id=raw_id,
                    )
                )

        self._session.add_all(links)


def _build_scalar_metric(raw: RawHealthSample) -> NormalizedHealthMetric | None:
    conversion = normalize_value(raw.sample_type, raw.raw_value, raw.raw_unit)
    confidence = conversion.confidence
    metric = NormalizedHealthMetric(
        user_id=raw.user_id,
        metric_type=raw.sample_type,
        start_time=raw.start_time,
        end_time=raw.end_time,
        value=conversion.value,
        unit=conversion.unit,
        confidence=confidence,
        source_sample_ids=[raw.source_sample_id],
        normalization_version=NORMALIZATION_VERSION,
    )
    # Temporary provenance anchor; discarded before persistence.
    metric._raw_health_sample_id = raw.id
    return metric


def _build_workout_candidate(
    raw: RawHealthSample,
) -> tuple[WorkoutCandidate | None, str | None]:
    conversion = normalize_value(raw.sample_type, raw.raw_value, raw.raw_unit)
    if conversion.confidence == 0.0:
        return None, conversion.rejected_reason or f"unknown unit {raw.raw_unit!r}"

    metadata = raw.source_metadata or {}
    modality = _parse_modality(metadata.get("modality"))
    duration = conversion.value
    end_time = raw.end_time or (
        raw.start_time + _duration_delta(duration) if duration else raw.start_time
    )

    return WorkoutCandidate(
        start_time=raw.start_time,
        end_time=end_time,
        duration=duration,
        modality=modality,
        distance=canonical_value_from_metadata(metadata, "distance_meters"),
        active_energy=canonical_value_from_metadata(metadata, "active_energy_kcal"),
        average_hr=canonical_value_from_metadata(metadata, "average_hr_bpm"),
        max_hr=canonical_value_from_metadata(metadata, "max_hr_bpm"),
        intensity_zone_distribution=_parse_zone_distribution(
            metadata.get("intensity_zone_distribution")
        ),
        perceived_exertion=_parse_int_metadata(metadata, "perceived_exertion", 1, 10),
        muscle_group_tags=_parse_string_list(metadata.get("muscle_group_tags")),
        source_sample_ids=[raw.source_sample_id],
        raw_health_sample_id=raw.id,
    ), None


def _build_sleep_candidate(
    raw: RawHealthSample,
) -> tuple[SleepCandidate | None, str | None]:
    conversion = normalize_value(raw.sample_type, raw.raw_value, raw.raw_unit)
    if conversion.confidence == 0.0:
        return None, conversion.rejected_reason or f"unknown unit {raw.raw_unit!r}"

    metadata = raw.source_metadata or {}
    duration = conversion.value
    end_time = raw.end_time or (
        raw.start_time + _duration_delta(duration) if duration else raw.start_time
    )

    return SleepCandidate(
        start_time=raw.start_time,
        end_time=end_time,
        duration=duration,
        sleep_stage_breakdown=_parse_stage_breakdown(metadata.get("stage_seconds")),
        interruptions=_parse_int_metadata(metadata, "interruptions", 0),
        quality_proxy=_parse_quality_proxy(metadata.get("quality_proxy")),
        source_sample_ids=[raw.source_sample_id],
        raw_health_sample_id=raw.id,
    ), None


def _workout_from_candidate(candidate: WorkoutCandidate, user_id: UUID) -> WorkoutSession:
    workout = WorkoutSession(
        user_id=user_id,
        start_time=candidate.start_time,
        end_time=candidate.end_time,
        modality=candidate.modality,
        distance=candidate.distance,
        duration=candidate.duration,
        active_energy=candidate.active_energy,
        average_hr=candidate.average_hr,
        max_hr=candidate.max_hr,
        intensity_zone_distribution=candidate.intensity_zone_distribution,
        perceived_exertion=candidate.perceived_exertion,
        muscle_group_tags=candidate.muscle_group_tags,
        confidence=candidate.confidence,
        normalization_version=NORMALIZATION_VERSION,
        source_sample_ids=candidate.source_sample_ids,
    )
    workout._raw_health_sample_id = candidate.raw_health_sample_id
    return workout


def _sleep_from_candidate(candidate: SleepCandidate, user_id: UUID) -> SleepSession:
    sleep = SleepSession(
        user_id=user_id,
        start_time=candidate.start_time,
        end_time=candidate.end_time,
        duration=candidate.duration,
        sleep_stage_breakdown=candidate.sleep_stage_breakdown,
        interruptions=candidate.interruptions,
        quality_proxy=candidate.quality_proxy,
        confidence=candidate.confidence,
        normalization_version=NORMALIZATION_VERSION,
        source_sample_ids=candidate.source_sample_ids,
    )
    sleep._raw_health_sample_id = candidate.raw_health_sample_id
    return sleep


def _parse_modality(value: Any) -> Modality:
    try:
        return Modality(str(value))
    except (ValueError, TypeError):
        return Modality.other


def _parse_zone_distribution(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items() if isinstance(v, (int, float))}
    return {}


def _parse_stage_breakdown(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items() if isinstance(v, (int, float))}
    return {}


def _parse_int_metadata(
    metadata: dict[str, Any],
    key: str,
    min_value: int,
    max_value: int | None = None,
) -> int | None:
    value = metadata.get(key)
    if not isinstance(value, int):
        return None
    if value < min_value:
        return None
    if max_value is not None and value > max_value:
        return None
    return value


def _parse_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _parse_quality_proxy(value: Any) -> float | None:
    if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
        return float(value)
    return None


def _duration_delta(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)
