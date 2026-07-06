"""Tests for P2-03 training load, density, VO2 trend, and recovery confidence."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

import pytest
from packages.fixtures import get_scenario
from packages.fixtures.loaders import load_fixture
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from baseline_api.db.models.enums import SensitiveNotePolicy
from baseline_api.db.models.sessions import SleepSession
from baseline_api.features.assembler import assemble_daily_features
from baseline_api.features.cardio import CardioSampleInput
from baseline_api.features.feature_types import FEATURE_VERSION
from baseline_api.features.recovery import compute_recovery_confidence
from baseline_api.features.sleep import SleepSessionInput
from baseline_api.features.training_load import (
    VO2SampleInput,
    WorkoutSessionInput,
    compute_training_load_features,
    compute_vo2_features,
)
from baseline_api.features.worker import _load_sleep_sessions, daily_analysis


def _target_date(dataset_name: str) -> dt.date:
    dataset = get_scenario(dataset_name)
    return dataset.start_date + dt.timedelta(days=dataset.days - 1)


def _workout_inputs(dataset_name: str) -> list[WorkoutSessionInput]:
    dataset = get_scenario(dataset_name)
    return [
        WorkoutSessionInput(
            session_id=workout.workout_id,
            start_time=workout.start_time,
            end_time=workout.end_time,
            modality=workout.modality,
            duration_seconds=workout.duration_seconds,
            distance_meters=workout.distance_meters,
            active_energy_kcal=workout.active_energy_kcal,
            average_hr_bpm=workout.average_hr_bpm,
            max_hr_bpm=workout.max_hr_bpm,
            intensity_zone_distribution=workout.intensity_zone_distribution,
            perceived_exertion=workout.perceived_exertion,
            muscle_group_tags=workout.muscle_group_tags,
            source_sample_ids=tuple(workout.source_sample_ids),
        )
        for workout in dataset.workouts
    ]


def _vo2_inputs(dataset_name: str) -> list[VO2SampleInput]:
    dataset = get_scenario(dataset_name)
    return [
        VO2SampleInput(
            sample_id=sample.sample_id,
            start_time=sample.start_time,
            value=sample.value,
            source_sample_ids=(sample.sample_id,),
        )
        for sample in dataset.samples
        if sample.metric_type == "vo2_max"
    ]


def _sleep_inputs(dataset_name: str) -> list[SleepSessionInput]:
    dataset = get_scenario(dataset_name)
    return [
        SleepSessionInput(
            start_time=session.start_time,
            end_time=session.end_time,
            duration_seconds=session.duration_seconds,
            sleep_stage_breakdown=session.stage_seconds,
            interruptions=session.interruptions,
            quality_proxy=session.quality_proxy,
            source_sample_ids=tuple(session.source_sample_ids),
        )
        for session in dataset.sleep_sessions
    ]


def _cardio_inputs(dataset_name: str, metric_type: str) -> list[CardioSampleInput]:
    dataset = get_scenario(dataset_name)
    return [
        CardioSampleInput(
            sample_id=sample.sample_id,
            start_time=sample.start_time,
            value=sample.value,
            source_sample_ids=(sample.sample_id,),
        )
        for sample in dataset.samples
        if sample.metric_type == metric_type
    ]


def _assert_feature_metadata(feature: Mapping[str, Any]) -> None:
    assert feature["feature_version"] == FEATURE_VERSION
    assert feature["calculation_metadata"]["formula_version"] == FEATURE_VERSION
    assert feature["calculation_metadata"]["deterministic"] is True


def _computed_value(feature: Mapping[str, Any], name: str) -> float:
    value = feature["values"][name]
    assert value["status"] == "computed"
    assert isinstance(value["value"], int | float)
    assert math.isfinite(value["value"])
    return float(value["value"])


def test_training_load_windows_computed_for_golden_fixture() -> None:
    target = _target_date("three_lower_body_sessions_six_days")

    feature = compute_training_load_features(
        target,
        _workout_inputs("three_lower_body_sessions_six_days"),
    )

    _assert_feature_metadata(feature)
    assert feature["status"] == "computed"
    assert "acute_load_units" in feature["values"]
    assert "chronic_load_units" in feature["values"]
    assert "acute_chronic_ratio" in feature["values"]
    assert "load_balance" in feature["values"]
    assert _computed_value(feature, "acute_load_units") >= 0
    assert _computed_value(feature, "chronic_load_units") >= 0
    assert _computed_value(feature, "acute_chronic_ratio") >= 0


def test_training_load_ewma_includes_calendar_rest_days() -> None:
    target = dt.date(2026, 1, 7)
    workouts = [
        WorkoutSessionInput(
            session_id="w-1",
            start_time=dt.datetime(2026, 1, 1, 7, 0, tzinfo=dt.UTC),
            end_time=dt.datetime(2026, 1, 1, 7, 30, tzinfo=dt.UTC),
            modality="strength_training",
            duration_seconds=30 * 60,
            active_energy_kcal=50,
            source_sample_ids=("w-1",),
        ),
        WorkoutSessionInput(
            session_id="w-2",
            start_time=dt.datetime(2026, 1, 7, 7, 0, tzinfo=dt.UTC),
            end_time=dt.datetime(2026, 1, 7, 7, 30, tzinfo=dt.UTC),
            modality="strength_training",
            duration_seconds=30 * 60,
            active_energy_kcal=50,
            source_sample_ids=("w-2",),
        ),
    ]

    feature = compute_training_load_features(
        target,
        workouts,
        acute_window_days=7,
        chronic_window_days=7,
    )

    assert _computed_value(feature, "acute_load_units") == pytest.approx(0.32)
    assert feature["data_quality"]["input_counts"]["acute_window_load_days"] == 2
    assert feature["data_quality"]["input_counts"]["acute_window_zero_load_days"] == 5


def test_training_load_window_is_insufficient_with_single_load_day() -> None:
    target = dt.date(2026, 1, 7)
    workouts = [
        WorkoutSessionInput(
            session_id="w-1",
            start_time=dt.datetime(2026, 1, 7, 7, 0, tzinfo=dt.UTC),
            end_time=dt.datetime(2026, 1, 7, 7, 30, tzinfo=dt.UTC),
            modality="cycling",
            duration_seconds=30 * 60,
            active_energy_kcal=50,
            source_sample_ids=("w-1",),
        )
    ]

    feature = compute_training_load_features(
        target,
        workouts,
        acute_window_days=7,
        chronic_window_days=28,
    )

    assert feature["values"]["acute_load_units"]["status"] == "insufficient_data"
    assert feature["values"]["chronic_load_units"]["status"] == "insufficient_data"
    assert "baseline_not_established_chronic_load" in feature["data_quality"]["flags"]


def test_training_load_uses_distance_modality_and_intensity_zones() -> None:
    target = dt.date(2026, 1, 7)
    low_intensity_walk = WorkoutSessionInput(
        session_id="walk",
        start_time=dt.datetime(2026, 1, 7, 7, 0, tzinfo=dt.UTC),
        end_time=dt.datetime(2026, 1, 7, 7, 30, tzinfo=dt.UTC),
        modality="walk",
        duration_seconds=30 * 60,
        distance_meters=5_000,
        intensity_zone_distribution={"zone_1": 30 * 60},
        source_sample_ids=("walk",),
    )
    high_intensity_run = WorkoutSessionInput(
        session_id="run",
        start_time=dt.datetime(2026, 1, 7, 8, 0, tzinfo=dt.UTC),
        end_time=dt.datetime(2026, 1, 7, 8, 30, tzinfo=dt.UTC),
        modality="run",
        duration_seconds=30 * 60,
        distance_meters=5_000,
        intensity_zone_distribution={"zone_4": 20 * 60, "zone_5": 10 * 60},
        source_sample_ids=("run",),
    )

    walk_feature = compute_training_load_features(target, [low_intensity_walk], min_load_days=1)
    run_feature = compute_training_load_features(target, [high_intensity_run], min_load_days=1)

    assert _computed_value(run_feature, "today_load_units") > _computed_value(
        walk_feature,
        "today_load_units",
    )


def test_workout_density_detects_three_lower_body_sessions_in_six_days() -> None:
    target = _target_date("three_lower_body_sessions_six_days")

    feature = compute_training_load_features(
        target,
        _workout_inputs("three_lower_body_sessions_six_days"),
        density_window_days=6,
    )

    muscle_density = feature["values"]["density_by_muscle_group"]["value"]
    assert "lower_body" in muscle_density
    assert muscle_density["lower_body"]["value"] == 3
    assert muscle_density["lower_body"]["window_days"] == 6


def test_vo2_trend_computed_when_samples_present() -> None:
    target = _target_date("vo2_improving_recovery_declining")

    feature = compute_vo2_features(
        target,
        _vo2_inputs("vo2_improving_recovery_declining"),
    )

    _assert_feature_metadata(feature)
    assert feature["status"] == "computed"
    assert _computed_value(feature, "trend_slope_per_week") > 0
    assert _computed_value(feature, "recent_value") > 0
    assert feature["values"]["trend_direction"]["value"] == "improving"


def test_vo2_trend_is_insufficient_data_without_samples() -> None:
    target = dt.date(2026, 1, 20)

    feature = compute_vo2_features(target, [])

    assert feature["status"] == "insufficient_data"
    assert feature["values"]["trend_slope_per_week"]["status"] == "insufficient_data"
    assert feature["values"]["recent_value"]["status"] == "insufficient_data"
    assert "missing_vo2_max" in feature["data_quality"]["flags"]


def test_recovery_confidence_high_when_inputs_complete() -> None:
    target = dt.date(2026, 1, 20)

    feature = compute_recovery_confidence(
        target,
        section_completeness={
            "sleep": 1.0,
            "hrv": 1.0,
            "rhr": 1.0,
            "training_load": 1.0,
            "vo2": 1.0,
        },
        flags=[],
    )

    _assert_feature_metadata(feature)
    assert feature["status"] == "computed"
    assert _computed_value(feature, "score") == pytest.approx(1.0)
    assert feature["values"]["level"]["value"] == "high"


def test_recovery_confidence_drops_with_missing_and_stale_inputs() -> None:
    target = dt.date(2026, 1, 20)

    feature = compute_recovery_confidence(
        target,
        section_completeness={
            "sleep": 1.0,
            "hrv": 0.0,
            "rhr": 1.0,
            "training_load": 1.0,
            "vo2": 1.0,
        },
        flags=["missing_heart_rate_variability", "stale_sleep"],
    )

    score = _computed_value(feature, "score")
    assert score < 1.0
    assert feature["values"]["level"]["value"] in {"low", "medium"}
    assert feature["data_quality"]["input_counts"]["missing_flags"] == 1
    assert feature["data_quality"]["input_counts"]["stale_flags"] == 1


def test_assembler_produces_complete_derived_daily_feature_fields() -> None:
    target = _target_date("high_hrv_good_sleep_low_load")

    bundle = assemble_daily_features(
        target,
        sleep_sessions=_sleep_inputs("high_hrv_good_sleep_low_load"),
        hrv_samples=_cardio_inputs("high_hrv_good_sleep_low_load", "heart_rate_variability"),
        rhr_samples=_cardio_inputs("high_hrv_good_sleep_low_load", "resting_heart_rate"),
        workouts=_workout_inputs("high_hrv_good_sleep_low_load"),
        vo2_samples=_vo2_inputs("high_hrv_good_sleep_low_load"),
        personal_sleep_need_hours=8.35,
        computed_at=dt.datetime(2026, 1, 25, 8, 0, tzinfo=dt.UTC),
    )

    derived_fields = bundle.to_derived_daily_feature_fields()

    assert derived_fields["feature_version"] == FEATURE_VERSION
    assert derived_fields["sleep_features"]["status"] == "computed"
    assert derived_fields["hrv_features"]["status"] == "computed"
    assert derived_fields["rhr_features"]["status"] == "computed"
    assert derived_fields["training_load_features"]["status"] == "computed"
    assert derived_fields["recovery_features"]["status"] == "computed"
    assert derived_fields["goal_features"]["status"] in {"computed", "insufficient_data"}
    assert derived_fields["data_quality"]["overall_completeness"] == pytest.approx(1.0)
    assert (
        derived_fields["data_quality"]["recovery_confidence_inputs"]["has_missing_inputs"] is False
    )
    assert derived_fields["data_quality"]["recovery_confidence_inputs"]["has_stale_inputs"] is False
    assert derived_fields["source_sample_ids"]


@pytest.mark.anyio
async def test_daily_analysis_worker_persists_derived_daily_feature(db_session: Any) -> None:
    dataset = get_scenario("high_hrv_good_sleep_low_load")
    loaded = load_fixture(db_session, dataset)
    target = dataset.start_date + dt.timedelta(days=dataset.days - 1)
    from baseline_api.db.models.checkin import DailyCheckIn

    checkin_model = DailyCheckIn(
        id=uuid4(),
        user_id=loaded.user.id,
        date=target,
        structured_notes={},
        sensitive_note_policy=SensitiveNotePolicy.exclude_from_external_llm,
    )
    db_session.add(checkin_model)
    db_session.flush()

    user_id_str = str(loaded.user.id)
    checkin_id_str = str(checkin_model.id)

    ctx = {"session_maker": lambda: db_session}
    result = await daily_analysis(
        ctx,
        checkin_id_str,
        user_id_str,
        target.isoformat(),
    )

    assert result["status"] == "success"
    assert result["feature_version"] == FEATURE_VERSION
    assert result["date"] == target.isoformat()

    from baseline_api.db.models.features import DerivedDailyFeature

    rows = list(
        db_session.exec(
            select(DerivedDailyFeature)
            .where(DerivedDailyFeature.user_id == UUID(user_id_str))
            .where(DerivedDailyFeature.date == target)
        ).all()
    )
    assert len(rows) == 1
    persisted = rows[0]
    assert persisted.feature_version == FEATURE_VERSION
    assert persisted.training_load_features["status"] == "computed"
    assert persisted.recovery_features["status"] == "computed"
    assert persisted.goal_features["status"] in {"computed", "insufficient_data"}


def test_worker_loads_sleep_sessions_by_effective_end_date(db_session: Any) -> None:
    dataset = get_scenario("high_hrv_good_sleep_low_load")
    loaded = load_fixture(db_session, dataset)
    target = dataset.start_date + dt.timedelta(days=dataset.days - 1)
    boundary_end_date = target - dt.timedelta(days=6)
    boundary_start = dt.datetime.combine(
        boundary_end_date - dt.timedelta(days=1),
        dt.time(23, 0),
        tzinfo=dt.UTC,
    )
    boundary_end = dt.datetime.combine(
        boundary_end_date,
        dt.time(7, 0),
        tzinfo=dt.UTC,
    )

    db_session.add(
        SleepSession(
            user_id=loaded.user.id,
            start_time=boundary_start,
            end_time=boundary_end,
            duration=8 * 3600,
            sleep_stage_breakdown={},
            interruptions=0,
            quality_proxy=0.9,
            normalization_version="test",
            source_sample_ids=["boundary-sleep"],
        )
    )
    db_session.flush()

    sleep_sessions = _load_sleep_sessions(db_session, loaded.user.id, target)

    assert any("boundary-sleep" in session.source_sample_ids for session in sleep_sessions)


@pytest.mark.anyio
async def test_daily_analysis_worker_is_idempotent(db_session: Any) -> None:
    dataset = get_scenario("high_hrv_good_sleep_low_load")
    loaded = load_fixture(db_session, dataset)
    target = dataset.start_date + dt.timedelta(days=dataset.days - 1)
    from baseline_api.db.models.checkin import DailyCheckIn

    checkin_model = DailyCheckIn(
        id=uuid4(),
        user_id=loaded.user.id,
        date=target,
        structured_notes={},
        sensitive_note_policy=SensitiveNotePolicy.exclude_from_external_llm,
    )
    db_session.add(checkin_model)
    db_session.flush()

    user_id_str = str(loaded.user.id)
    checkin_id_str = str(checkin_model.id)

    ctx = {"session_maker": lambda: db_session}
    first = await daily_analysis(
        ctx,
        checkin_id_str,
        user_id_str,
        target.isoformat(),
    )
    first_id = first["derived_daily_feature_id"]

    second = await daily_analysis(
        ctx,
        checkin_id_str,
        user_id_str,
        target.isoformat(),
    )
    second_id = second["derived_daily_feature_id"]

    assert first_id == second_id

    from baseline_api.db.models.features import DerivedDailyFeature

    rows = list(
        db_session.exec(
            select(DerivedDailyFeature)
            .where(DerivedDailyFeature.user_id == UUID(user_id_str))
            .where(DerivedDailyFeature.date == target)
        ).all()
    )
    assert len(rows) == 1
    persisted = rows[0]

    db_session.add(
        DerivedDailyFeature(
            user_id=persisted.user_id,
            date=persisted.date,
            feature_version=persisted.feature_version,
            sleep_features=persisted.sleep_features,
            hrv_features=persisted.hrv_features,
            rhr_features=persisted.rhr_features,
            training_load_features=persisted.training_load_features,
            recovery_features=persisted.recovery_features,
            goal_features=persisted.goal_features,
            data_quality=persisted.data_quality,
            anomaly_flags=persisted.anomaly_flags,
            computed_at=persisted.computed_at,
            source_sample_ids=persisted.source_sample_ids,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_goal_indicators_cover_all_prd_categories() -> None:
    target = _target_date("high_hrv_good_sleep_low_load")

    bundle = assemble_daily_features(
        target,
        sleep_sessions=_sleep_inputs("high_hrv_good_sleep_low_load"),
        hrv_samples=_cardio_inputs("high_hrv_good_sleep_low_load", "heart_rate_variability"),
        rhr_samples=_cardio_inputs("high_hrv_good_sleep_low_load", "resting_heart_rate"),
        workouts=_workout_inputs("high_hrv_good_sleep_low_load"),
        vo2_samples=_vo2_inputs("high_hrv_good_sleep_low_load"),
        personal_sleep_need_hours=8.35,
        computed_at=dt.datetime(2026, 1, 25, 8, 0, tzinfo=dt.UTC),
    )

    goal_features = bundle.to_derived_daily_feature_fields()["goal_features"]
    indicators = goal_features["values"]["goal_indicators"]["value"]
    expected_categories = [
        "vo2_max",
        "strength",
        "recovery",
        "sleep",
        "cognitive_performance",
        "long_term_wellness",
    ]
    for category in expected_categories:
        assert category in indicators, f"missing indicator for {category}"
        indicator = indicators[category]
        assert indicator["status"] in {"computed", "unavailable"}
        assert indicator["confidence"] in {"low", "medium", "high"}
        if indicator["status"] == "unavailable":
            assert indicator["evidence_refs"] == []
            assert indicator["missing_data"]
