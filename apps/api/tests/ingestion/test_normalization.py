"""Tests for P1-02 health data normalization."""

from __future__ import annotations

import datetime as dt
from uuid import UUID, uuid4

import pytest
from packages.fixtures import get_scenario
from sqlmodel import Session, col, select

from baseline_api.db.models.enums import MetricType, Modality, PrivacyMode
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
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.ingestion.normalization import NormalizationService
from baseline_api.ingestion.normalization.worker import normalize_health_batch


def _seed_user(session: Session) -> User:
    user = User(
        privacy_mode=PrivacyMode.local_only,
        active_consent_version="v1",
    )
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


def _create_batch(session: Session, user_id: UUID) -> HealthImportBatch:
    batch = HealthImportBatch(
        user_id=user_id,
        client_sync_id=f"test-batch-{uuid4()}",
        request_hash="test-hash",
        source_platform="apple_health",
        source_device="test-watch",
        timezone="UTC",
        next_anchor="test-anchor",
        accepted_count=0,
        duplicate_count=0,
        rejected_count=0,
        imported_at=dt.datetime.now(dt.UTC),
    )
    session.add(batch)
    session.flush()
    return batch


def _raw_sample(
    session: Session,
    batch: HealthImportBatch,
    *,
    source_sample_id: str,
    sample_type: MetricType,
    start_time: dt.datetime,
    raw_value: float,
    raw_unit: str,
    end_time: dt.datetime | None = None,
    source_metadata: dict[str, object] | None = None,
) -> RawHealthSample:
    raw = RawHealthSample(
        user_id=batch.user_id,
        source_platform="apple_health",
        source_device="test-watch",
        source_sample_id=source_sample_id,
        sample_type=sample_type,
        start_time=start_time,
        end_time=end_time,
        raw_value=raw_value,
        raw_unit=raw_unit,
        source_metadata=source_metadata or {},
        imported_at=batch.imported_at,
        import_batch_id=batch.id,
    )
    session.add(raw)
    session.flush()
    return raw


def test_scalar_unit_normalization_converts_to_canonical_units(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    start = dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC)

    scalar_samples = [
        ("hrv-ms", MetricType.heart_rate_variability, 52.4, "ms", "ms"),
        ("rhr-bpm", MetricType.resting_heart_rate, 55.0, "count/min", "bpm"),
        ("steps-count", MetricType.steps, 8432.0, "count", "count"),
        ("energy-kj", MetricType.active_energy, 1000.0, "kJ", "kcal"),
        ("temp-f", MetricType.body_temperature, 98.6, "F", "degC"),
    ]

    for source_id, sample_type, value, unit, _ in scalar_samples:
        _raw_sample(
            db_session,
            batch,
            source_sample_id=source_id,
            sample_type=sample_type,
            start_time=start,
            raw_value=value,
            raw_unit=unit,
        )

    _raw_sample(
        db_session,
        batch,
        source_sample_id="sleep-hours",
        sample_type=MetricType.sleep_duration,
        start_time=start + dt.timedelta(hours=12),
        end_time=start + dt.timedelta(hours=19, minutes=30),
        raw_value=7.5,
        raw_unit="h",
    )
    _raw_sample(
        db_session,
        batch,
        source_sample_id="workout-min",
        sample_type=MetricType.workout,
        start_time=start + dt.timedelta(hours=2),
        end_time=start + dt.timedelta(hours=2, minutes=45),
        raw_value=45.0,
        raw_unit="min",
        source_metadata={"modality": "run"},
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.normalized_metric_count == len(scalar_samples)
    assert result.workout_count == 1
    assert result.sleep_count == 1
    db_session.commit()

    metrics = list(
        db_session.exec(
            select(NormalizedHealthMetric).where(NormalizedHealthMetric.user_id == user.id)
        ).all()
    )
    by_source = {m.source_sample_ids[0]: m for m in metrics}

    assert by_source["hrv-ms"].value == pytest.approx(52.4)
    assert by_source["hrv-ms"].unit == "ms"
    assert by_source["rhr-bpm"].unit == "bpm"
    assert by_source["steps-count"].unit == "count"
    assert by_source["energy-kj"].value == pytest.approx(239.005736)
    assert by_source["energy-kj"].unit == "kcal"
    assert by_source["temp-f"].value == pytest.approx(37.0)
    assert by_source["temp-f"].unit == "degC"

    sleep = db_session.exec(select(SleepSession)).one()
    assert sleep.duration == pytest.approx(27000.0)
    assert sleep.source_sample_ids == ["sleep-hours"]

    workout = db_session.exec(select(WorkoutSession)).one()
    assert workout.duration == pytest.approx(2700.0)
    assert workout.source_sample_ids == ["workout-min"]

    assert sleep.normalization_version == "p1-02-v1"
    assert workout.normalization_version == "p1-02-v1"

    for metric in metrics:
        assert metric.normalization_version == "p1-02-v1"
        assert metric.confidence == 1.0


def test_unknown_unit_is_rejected_without_silent_coercion(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    _raw_sample(
        db_session,
        batch,
        source_sample_id="unknown-unit",
        sample_type=MetricType.heart_rate_variability,
        start_time=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
        raw_value=52.4,
        raw_unit="unknown",
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.normalized_metric_count == 0
    assert any("unknown" in warning for warning in result.warnings)
    db_session.commit()

    metrics = list(
        db_session.exec(
            select(NormalizedHealthMetric).where(NormalizedHealthMetric.user_id == user.id)
        ).all()
    )
    assert metrics == []


def test_workout_classification_from_metadata(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    start = dt.datetime(2026, 1, 15, 7, 0, tzinfo=dt.UTC)
    end = start + dt.timedelta(seconds=2700)
    _raw_sample(
        db_session,
        batch,
        source_sample_id="workout-1",
        sample_type=MetricType.workout,
        start_time=start,
        end_time=end,
        raw_value=2700.0,
        raw_unit="s",
        source_metadata={
            "modality": "run",
            "distance_meters": 5200.0,
            "active_energy_kcal": 320.0,
            "average_hr_bpm": 142.0,
            "max_hr_bpm": 168.0,
            "intensity_zone_distribution": {"z1": 0.1, "z2": 0.5, "z3": 0.3, "z4": 0.1},
            "perceived_exertion": 7,
            "muscle_group_tags": ["cardio", "lower_body"],
        },
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.workout_count == 1
    assert result.normalized_metric_count == 0
    db_session.commit()

    workout = db_session.exec(select(WorkoutSession)).one()
    assert workout.modality == Modality.run
    assert workout.duration == pytest.approx(2700.0)
    assert workout.distance == pytest.approx(5200.0)
    assert workout.active_energy == pytest.approx(320.0)
    assert workout.average_hr == pytest.approx(142.0)
    assert workout.max_hr == pytest.approx(168.0)
    assert workout.intensity_zone_distribution == {"z1": 0.1, "z2": 0.5, "z3": 0.3, "z4": 0.1}
    assert workout.perceived_exertion == 7
    assert workout.muscle_group_tags == ["cardio", "lower_body"]
    assert workout.source_sample_ids == ["workout-1"]


def test_sleep_normalization_from_duration_sample(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    start = dt.datetime(2026, 1, 15, 22, 0, tzinfo=dt.UTC)
    end = start + dt.timedelta(hours=7, minutes=30)
    _raw_sample(
        db_session,
        batch,
        source_sample_id="sleep-1",
        sample_type=MetricType.sleep_duration,
        start_time=start,
        end_time=end,
        raw_value=7.5,
        raw_unit="h",
        source_metadata={
            "stage_seconds": {"awake": 900.0, "core": 16200.0, "deep": 5400.0, "rem": 4500.0},
            "interruptions": 2,
            "quality_proxy": 0.82,
        },
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.sleep_count == 1
    assert result.normalized_metric_count == 0
    db_session.commit()

    sleep = db_session.exec(select(SleepSession)).one()
    assert sleep.duration == pytest.approx(27000.0)
    assert sleep.sleep_stage_breakdown == {
        "awake": 900.0,
        "core": 16200.0,
        "deep": 5400.0,
        "rem": 4500.0,
    }
    assert sleep.interruptions == 2
    assert sleep.quality_proxy == pytest.approx(0.82)
    assert sleep.source_sample_ids == ["sleep-1"]


def test_golden_fixture_produces_expected_normalized_counts(db_session: Session) -> None:
    dataset = get_scenario("high_hrv_good_sleep_low_load")
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    imported_at = dt.datetime.combine(dataset.start_date, dt.time(0, 0), tzinfo=dt.UTC)
    batch.imported_at = imported_at

    for sample in dataset.samples:
        db_session.add(
            RawHealthSample(
                user_id=user.id,
                source_platform="apple_health_synthetic",
                source_device="Baseline Synthetic Watch",
                source_sample_id=sample.sample_id,
                sample_type=MetricType(sample.metric_type),
                start_time=sample.start_time,
                end_time=sample.end_time,
                raw_value=sample.value,
                raw_unit=sample.unit,
                source_metadata=sample.metadata,
                imported_at=imported_at,
                import_batch_id=batch.id,
            )
        )
    for workout in dataset.workouts:
        db_session.add(
            RawHealthSample(
                user_id=user.id,
                source_platform="apple_health_synthetic",
                source_device="Baseline Synthetic Watch",
                source_sample_id=workout.workout_id,
                sample_type=MetricType.workout,
                start_time=workout.start_time,
                end_time=workout.end_time,
                raw_value=workout.duration_seconds,
                raw_unit="s",
                source_metadata={
                    "modality": workout.modality,
                    "distance_meters": workout.distance_meters,
                    "active_energy_kcal": workout.active_energy_kcal,
                    "average_hr_bpm": workout.average_hr_bpm,
                    "max_hr_bpm": workout.max_hr_bpm,
                    "intensity_zone_distribution": workout.intensity_zone_distribution,
                    "perceived_exertion": workout.perceived_exertion,
                    "muscle_group_tags": workout.muscle_group_tags,
                },
                imported_at=imported_at,
                import_batch_id=batch.id,
            )
        )
    db_session.flush()

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    # Sleep-duration samples become SleepSession records, not scalar metrics.
    expected_metric_count = len(dataset.samples) - len(dataset.sleep_sessions)
    assert result.normalized_metric_count == expected_metric_count
    assert result.workout_count == len(dataset.workouts)
    assert result.sleep_count == len(dataset.sleep_sessions)
    db_session.commit()


def test_same_type_overlap_keeps_longer_session(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    base = dt.datetime(2026, 1, 15, 22, 0, tzinfo=dt.UTC)

    _raw_sample(
        db_session,
        batch,
        source_sample_id="long-sleep",
        sample_type=MetricType.sleep_duration,
        start_time=base,
        end_time=base + dt.timedelta(hours=8),
        raw_value=8.0,
        raw_unit="h",
    )
    _raw_sample(
        db_session,
        batch,
        source_sample_id="short-sleep",
        sample_type=MetricType.sleep_duration,
        start_time=base + dt.timedelta(hours=1),
        end_time=base + dt.timedelta(hours=3),
        raw_value=2.0,
        raw_unit="h",
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.sleep_count == 1
    db_session.commit()

    sleep = db_session.exec(select(SleepSession)).one()
    assert sleep.duration == pytest.approx(28800.0)
    assert sleep.source_sample_ids == ["long-sleep"]


def test_same_type_overlap_keeps_longer_session_when_shorter_starts_first(
    db_session: Session,
) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    base = dt.datetime(2026, 1, 15, 22, 0, tzinfo=dt.UTC)

    _raw_sample(
        db_session,
        batch,
        source_sample_id="short-sleep",
        sample_type=MetricType.sleep_duration,
        start_time=base,
        end_time=base + dt.timedelta(hours=2),
        raw_value=2.0,
        raw_unit="h",
    )
    _raw_sample(
        db_session,
        batch,
        source_sample_id="long-sleep",
        sample_type=MetricType.sleep_duration,
        start_time=base + dt.timedelta(hours=1),
        end_time=base + dt.timedelta(hours=9),
        raw_value=8.0,
        raw_unit="h",
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.sleep_count == 1
    assert any("Dropped shorter overlapping" in warning for warning in result.warnings)
    db_session.commit()

    sleep = db_session.exec(select(SleepSession)).one()
    assert sleep.duration == pytest.approx(28800.0)
    assert sleep.source_sample_ids == ["long-sleep"]


def test_unknown_workout_unit_is_rejected_with_warning(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    _raw_sample(
        db_session,
        batch,
        source_sample_id="workout-bad-unit",
        sample_type=MetricType.workout,
        start_time=dt.datetime(2026, 1, 15, 7, 0, tzinfo=dt.UTC),
        end_time=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
        raw_value=1.0,
        raw_unit="fortnight",
        source_metadata={"modality": "run"},
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.workout_count == 0
    assert any("Rejected workout sample workout-bad-unit" in warning for warning in result.warnings)
    db_session.commit()

    workouts = list(db_session.exec(select(WorkoutSession)).all())
    assert workouts == []


def test_unknown_sleep_unit_is_rejected_with_warning(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    _raw_sample(
        db_session,
        batch,
        source_sample_id="sleep-bad-unit",
        sample_type=MetricType.sleep_duration,
        start_time=dt.datetime(2026, 1, 15, 22, 0, tzinfo=dt.UTC),
        end_time=dt.datetime(2026, 1, 15, 23, 0, tzinfo=dt.UTC),
        raw_value=1.0,
        raw_unit="epoch",
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.sleep_count == 0
    assert any("Rejected sleep sample sleep-bad-unit" in warning for warning in result.warnings)
    db_session.commit()

    sleeps = list(db_session.exec(select(SleepSession)).all())
    assert sleeps == []


def test_cross_type_overlap_retains_both_with_reduced_confidence(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    start = dt.datetime(2026, 1, 15, 22, 0, tzinfo=dt.UTC)

    _raw_sample(
        db_session,
        batch,
        source_sample_id="sleep-cross",
        sample_type=MetricType.sleep_duration,
        start_time=start,
        end_time=start + dt.timedelta(hours=8),
        raw_value=8.0,
        raw_unit="h",
    )
    _raw_sample(
        db_session,
        batch,
        source_sample_id="workout-cross",
        sample_type=MetricType.workout,
        start_time=start + dt.timedelta(hours=2),
        end_time=start + dt.timedelta(hours=3),
        raw_value=3600.0,
        raw_unit="s",
        source_metadata={"modality": "strength"},
    )

    service = NormalizationService(db_session)
    result = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)

    assert result.sleep_count == 1
    assert result.workout_count == 1
    assert any("Cross-type overlap" in warning for warning in result.warnings)
    db_session.commit()

    workout = db_session.exec(
        select(WorkoutSession).where(
            col(WorkoutSession.source_sample_ids).contains(["workout-cross"])
        )
    ).one()
    sleep = db_session.exec(
        select(SleepSession).where(col(SleepSession.source_sample_ids).contains(["sleep-cross"]))
    ).one()
    assert workout.confidence == pytest.approx(0.5)
    assert sleep.confidence == pytest.approx(0.5)


def test_normalization_is_idempotent_across_re_runs(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    start = dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC)

    for index in range(3):
        _raw_sample(
            db_session,
            batch,
            source_sample_id=f"hrv-{index}",
            sample_type=MetricType.heart_rate_variability,
            start_time=start + dt.timedelta(hours=index),
            raw_value=50.0 + index,
            raw_unit="ms",
        )
    _raw_sample(
        db_session,
        batch,
        source_sample_id="workout-1",
        sample_type=MetricType.workout,
        start_time=start,
        end_time=start + dt.timedelta(hours=1),
        raw_value=3600.0,
        raw_unit="s",
        source_metadata={"modality": "run"},
    )
    _raw_sample(
        db_session,
        batch,
        source_sample_id="sleep-1",
        sample_type=MetricType.sleep_duration,
        start_time=start + dt.timedelta(hours=12),
        end_time=start + dt.timedelta(hours=19),
        raw_value=7.0,
        raw_unit="h",
    )

    service = NormalizationService(db_session)
    first = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)
    db_session.commit()

    second = service.normalize_batch(import_batch_id=batch.id, user_id=user.id)
    db_session.commit()

    assert first.normalized_metric_count == second.normalized_metric_count == 3
    assert first.workout_count == second.workout_count == 1
    assert first.sleep_count == second.sleep_count == 1

    metrics = list(
        db_session.exec(
            select(NormalizedHealthMetric).where(NormalizedHealthMetric.user_id == user.id)
        ).all()
    )
    workouts = list(
        db_session.exec(select(WorkoutSession).where(WorkoutSession.user_id == user.id)).all()
    )
    sleeps = list(
        db_session.exec(select(SleepSession).where(SleepSession.user_id == user.id)).all()
    )
    assert len(metrics) == 3
    assert len(workouts) == 1
    assert len(sleeps) == 1

    metric_ids = [m.id for m in metrics]
    links = list(
        db_session.exec(
            select(NormalizedHealthMetricSourceSample).where(
                col(NormalizedHealthMetricSourceSample.normalized_health_metric_id).in_(metric_ids)
            )
        ).all()
    )
    assert len(links) == 3


def test_provenance_links_persisted_for_all_outputs(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    start = dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC)

    hrv = _raw_sample(
        db_session,
        batch,
        source_sample_id="hrv-prov",
        sample_type=MetricType.heart_rate_variability,
        start_time=start,
        raw_value=55.0,
        raw_unit="ms",
    )
    workout_raw = _raw_sample(
        db_session,
        batch,
        source_sample_id="workout-prov",
        sample_type=MetricType.workout,
        start_time=start + dt.timedelta(hours=2),
        end_time=start + dt.timedelta(hours=3),
        raw_value=3600.0,
        raw_unit="s",
        source_metadata={"modality": "kettlebell"},
    )
    sleep_raw = _raw_sample(
        db_session,
        batch,
        source_sample_id="sleep-prov",
        sample_type=MetricType.sleep_duration,
        start_time=start + dt.timedelta(hours=10),
        end_time=start + dt.timedelta(hours=17),
        raw_value=7.0,
        raw_unit="h",
    )

    service = NormalizationService(db_session)
    service.normalize_batch(import_batch_id=batch.id, user_id=user.id)
    db_session.commit()

    metric_links = list(
        db_session.exec(
            select(NormalizedHealthMetricSourceSample).where(
                NormalizedHealthMetricSourceSample.raw_health_sample_id == hrv.id
            )
        ).all()
    )
    workout_links = list(
        db_session.exec(
            select(WorkoutSessionSourceSample).where(
                WorkoutSessionSourceSample.raw_health_sample_id == workout_raw.id
            )
        ).all()
    )
    sleep_links = list(
        db_session.exec(
            select(SleepSessionSourceSample).where(
                SleepSessionSourceSample.raw_health_sample_id == sleep_raw.id
            )
        ).all()
    )

    assert len(metric_links) == 1
    assert len(workout_links) == 1
    assert len(sleep_links) == 1


def test_provenance_links_correct_row_when_source_sample_id_is_shared(db_session: Session) -> None:
    """P1-01 dedupes raw samples by source_sample_id + content_hash.

    Two rows with the same source_sample_id but different content_hash must each
    link to their own normalized output, not collapse to a single raw row.
    """
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    start = dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC)

    raw_a = RawHealthSample(
        user_id=user.id,
        source_platform="apple_health",
        source_device="test-watch",
        source_sample_id="shared-hrv",
        content_hash="hash-a",
        sample_type=MetricType.heart_rate_variability,
        start_time=start,
        raw_value=55.0,
        raw_unit="ms",
        source_metadata={},
        imported_at=batch.imported_at,
        import_batch_id=batch.id,
    )
    raw_b = RawHealthSample(
        user_id=user.id,
        source_platform="apple_health",
        source_device="test-watch",
        source_sample_id="shared-hrv",
        content_hash="hash-b",
        sample_type=MetricType.heart_rate_variability,
        start_time=start + dt.timedelta(hours=1),
        raw_value=65.0,
        raw_unit="ms",
        source_metadata={},
        imported_at=batch.imported_at,
        import_batch_id=batch.id,
    )
    db_session.add(raw_a)
    db_session.add(raw_b)
    db_session.flush()

    service = NormalizationService(db_session)
    service.normalize_batch(import_batch_id=batch.id, user_id=user.id)
    db_session.commit()

    metrics = list(
        db_session.exec(
            select(NormalizedHealthMetric).where(NormalizedHealthMetric.user_id == user.id)
        ).all()
    )
    assert len(metrics) == 2

    by_value = {m.value: m for m in metrics}
    assert set(by_value.keys()) == {55.0, 65.0}

    metric_a = by_value[55.0]
    metric_b = by_value[65.0]

    link_a = db_session.exec(
        select(NormalizedHealthMetricSourceSample).where(
            NormalizedHealthMetricSourceSample.normalized_health_metric_id == metric_a.id
        )
    ).one()
    link_b = db_session.exec(
        select(NormalizedHealthMetricSourceSample).where(
            NormalizedHealthMetricSourceSample.normalized_health_metric_id == metric_b.id
        )
    ).one()

    assert link_a.raw_health_sample_id == raw_a.id
    assert link_b.raw_health_sample_id == raw_b.id


class _FakeSessionContext:
    """Context manager that yields an existing session without closing it."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *args: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_arq_worker_entrypoint_normalizes_batch(db_session: Session) -> None:
    user = _seed_user(db_session)
    batch = _create_batch(db_session, user.id)
    _raw_sample(
        db_session,
        batch,
        source_sample_id="worker-hrv",
        sample_type=MetricType.heart_rate_variability,
        start_time=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
        raw_value=55.0,
        raw_unit="ms",
    )

    result = await normalize_health_batch(
        {"session_maker": lambda: _FakeSessionContext(db_session)},
        str(batch.id),
        str(user.id),
    )

    assert result["import_batch_id"] == str(batch.id)
    assert result["normalized_metric_count"] == 1
    assert result["workout_count"] == 0
    assert result["sleep_count"] == 0
