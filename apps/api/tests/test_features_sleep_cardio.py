"""Tests for sleep and cardiovascular feature calculations."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from packages.fixtures import get_scenario

from baseline_api.features.assembler import assemble_daily_features
from baseline_api.features.cardio import (
    CardioSampleInput,
    compute_hrv_features,
    compute_rhr_features,
)
from baseline_api.features.feature_types import FEATURE_VERSION
from baseline_api.features.sleep import SleepSessionInput, compute_sleep_features


def _target_date(dataset_name: str) -> dt.date:
    dataset = get_scenario(dataset_name)
    return dataset.start_date + dt.timedelta(days=dataset.days - 1)


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


def _walk_values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        current = [value] if value.get("status") == "computed" and "value" in value else []
        for child in value.values():
            current.extend(_walk_values(child))
        return current
    if isinstance(value, list):
        nested: list[dict[str, Any]] = []
        for child in value:
            nested.extend(_walk_values(child))
        return nested
    return []


def test_sleep_features_match_golden_fixture_formula() -> None:
    target = _target_date("high_hrv_good_sleep_low_load")

    feature = compute_sleep_features(
        target,
        _sleep_inputs("high_hrv_good_sleep_low_load"),
        personal_sleep_need_hours=8.35,
    )

    _assert_feature_metadata(feature)
    assert feature["status"] == "computed"
    assert _computed_value(feature, "duration_hours") == pytest.approx(7.69)
    assert _computed_value(feature, "sleep_debt_hours") == pytest.approx(0.66)
    assert _computed_value(feature, "consistency_minutes") == pytest.approx(11.43)
    assert _computed_value(feature, "quality_proxy") == pytest.approx(0.826)
    assert feature["data_quality"]["flags"] == []


def test_cardio_features_match_golden_fixture_baselines() -> None:
    target = _target_date("low_hrv_high_rhr_poor_sleep")

    hrv = compute_hrv_features(
        target,
        _cardio_inputs("low_hrv_high_rhr_poor_sleep", "heart_rate_variability"),
    )
    rhr = compute_rhr_features(
        target,
        _cardio_inputs("low_hrv_high_rhr_poor_sleep", "resting_heart_rate"),
    )

    _assert_feature_metadata(hrv)
    _assert_feature_metadata(rhr)
    assert hrv["status"] == "computed"
    assert _computed_value(hrv, "today_ms") == pytest.approx(18.5)
    assert _computed_value(hrv, "baseline_ms") == pytest.approx(29.75)
    assert _computed_value(hrv, "deviation_ms") == pytest.approx(-11.25)
    assert _computed_value(hrv, "deviation_pct") == pytest.approx(-37.82)
    assert rhr["status"] == "computed"
    assert _computed_value(rhr, "today_bpm") == pytest.approx(75.1)
    assert _computed_value(rhr, "baseline_bpm") == pytest.approx(70.25)
    assert _computed_value(rhr, "deviation_bpm") == pytest.approx(4.85)
    assert _computed_value(rhr, "deviation_pct") == pytest.approx(6.9)


def test_missing_target_measurements_are_explicit_gaps() -> None:
    target = _target_date("missing_hrv")

    feature = compute_hrv_features(
        target,
        _cardio_inputs("missing_hrv", "heart_rate_variability"),
    )

    assert feature["status"] == "insufficient_data"
    assert feature["values"]["today_ms"]["status"] == "insufficient_data"
    assert feature["values"]["today_ms"]["reason"] == "missing_input"
    assert "missing_heart_rate_variability" in feature["data_quality"]["flags"]
    assert "stale_heart_rate_variability" in feature["data_quality"]["flags"]


def test_baseline_not_established_is_first_class_status() -> None:
    target = dt.date(2026, 1, 8)
    samples = [
        CardioSampleInput(
            sample_id=f"hrv-{index}",
            start_time=dt.datetime(2026, 1, 5 + index, 6, 0, tzinfo=dt.UTC),
            value=55.0 + index,
            source_sample_ids=(f"hrv-{index}",),
        )
        for index in range(4)
    ]

    feature = compute_hrv_features(target, samples)

    assert feature["status"] == "baseline_not_established"
    assert feature["values"]["today_ms"]["status"] == "computed"
    assert feature["values"]["baseline_ms"]["status"] == "baseline_not_established"
    assert "baseline_not_established_heart_rate_variability" in feature["data_quality"]["flags"]


def test_sleep_missing_stale_anomaly_and_conflict_flags_are_reported() -> None:
    target = _target_date("stale_sleep")
    stale_feature = compute_sleep_features(target, _sleep_inputs("stale_sleep"))

    assert stale_feature["status"] == "insufficient_data"
    assert "missing_sleep" in stale_feature["data_quality"]["flags"]
    assert "stale_sleep" in stale_feature["data_quality"]["flags"]

    conflict_feature = compute_sleep_features(
        dt.date(2026, 1, 20),
        [
            SleepSessionInput(
                start_time=dt.datetime(2026, 1, 19, 22, 0, tzinfo=dt.UTC),
                end_time=dt.datetime(2026, 1, 20, 6, 0, tzinfo=dt.UTC),
                duration_seconds=8 * 3600,
                quality_proxy=0.8,
                source_sample_ids=("sleep-a",),
            ),
            SleepSessionInput(
                start_time=dt.datetime(2026, 1, 19, 23, 0, tzinfo=dt.UTC),
                end_time=dt.datetime(2026, 1, 20, 7, 0, tzinfo=dt.UTC),
                duration_seconds=8 * 3600,
                quality_proxy=1.2,
                source_sample_ids=("sleep-b",),
            ),
        ],
    )

    assert "conflicting_sleep_sessions" in conflict_feature["data_quality"]["flags"]
    assert "anomalous_sleep_quality_proxy" in conflict_feature["data_quality"]["flags"]
    assert conflict_feature["values"]["duration_hours"]["status"] == "insufficient_data"
    assert conflict_feature["values"]["duration_hours"]["reason"] == "conflicting_input"


def test_future_records_do_not_suppress_stale_flags() -> None:
    target = dt.date(2026, 1, 20)

    sleep = compute_sleep_features(
        target,
        [
            SleepSessionInput(
                start_time=dt.datetime(2026, 1, 21, 22, 0, tzinfo=dt.UTC),
                end_time=dt.datetime(2026, 1, 22, 6, 0, tzinfo=dt.UTC),
                duration_seconds=8 * 3600,
                source_sample_ids=("future-sleep",),
            )
        ],
    )
    hrv = compute_hrv_features(
        target,
        [
            CardioSampleInput(
                sample_id="future-hrv",
                start_time=dt.datetime(2026, 1, 22, 6, 0, tzinfo=dt.UTC),
                value=55.0,
                source_sample_ids=("future-hrv",),
            )
        ],
    )

    assert "missing_sleep" in sleep["data_quality"]["flags"]
    assert "stale_sleep" in sleep["data_quality"]["flags"]
    assert "missing_heart_rate_variability" in hrv["data_quality"]["flags"]
    assert "stale_heart_rate_variability" in hrv["data_quality"]["flags"]


def test_duration_only_sleep_is_used_with_quality_flag() -> None:
    target = dt.date(2026, 1, 20)

    feature = compute_sleep_features(
        target,
        [
            SleepSessionInput(
                start_time=dt.datetime(2026, 1, 19, 22, 30, tzinfo=dt.UTC),
                end_time=None,
                duration_seconds=7.5 * 3600,
                quality_proxy=0.7,
                source_sample_ids=("duration-only-sleep",),
            )
        ],
        min_consistency_sessions=1,
    )

    assert feature["status"] == "computed"
    assert _computed_value(feature, "duration_hours") == pytest.approx(7.5)
    assert "missing_sleep_end_time" in feature["data_quality"]["flags"]
    assert feature["source_sample_ids"] == ["duration-only-sleep"]


def test_cardio_anomalies_and_conflicts_are_reported_without_fabrication() -> None:
    target = dt.date(2026, 1, 20)
    samples = [
        CardioSampleInput(
            sample_id=f"rhr-baseline-{index}",
            start_time=dt.datetime(2026, 1, 5 + index, 6, 0, tzinfo=dt.UTC),
            value=52.0 + index % 2,
            source_sample_ids=(f"rhr-baseline-{index}",),
        )
        for index in range(8)
    ]
    samples.extend(
        [
            CardioSampleInput(
                sample_id="rhr-conflict-a",
                start_time=dt.datetime(2026, 1, 20, 6, 0, tzinfo=dt.UTC),
                value=55.0,
                source_sample_ids=("rhr-conflict-a",),
            ),
            CardioSampleInput(
                sample_id="rhr-conflict-b",
                start_time=dt.datetime(2026, 1, 20, 6, 5, tzinfo=dt.UTC),
                value=82.0,
                source_sample_ids=("rhr-conflict-b",),
            ),
            CardioSampleInput(
                sample_id="rhr-anomaly",
                start_time=dt.datetime(2026, 1, 20, 6, 10, tzinfo=dt.UTC),
                value=250.0,
                source_sample_ids=("rhr-anomaly",),
            ),
        ]
    )

    feature = compute_rhr_features(target, samples)

    assert feature["status"] == "computed"
    assert "conflicting_resting_heart_rate" in feature["data_quality"]["flags"]
    assert "anomalous_resting_heart_rate" in feature["data_quality"]["flags"]
    assert _computed_value(feature, "today_bpm") == pytest.approx(68.5)


def test_low_confidence_inputs_are_reported_and_provenance_does_not_fallback() -> None:
    target = dt.date(2026, 1, 20)
    samples = [
        CardioSampleInput(
            sample_id=f"fallback-only-{index}",
            start_time=dt.datetime(2026, 1, 5 + index, 6, 0, tzinfo=dt.UTC),
            value=55.0,
        )
        for index in range(7)
    ]
    samples.append(
        CardioSampleInput(
            sample_id="target-no-provenance",
            start_time=dt.datetime(2026, 1, 20, 6, 0, tzinfo=dt.UTC),
            value=54.0,
            confidence=0.4,
        )
    )

    feature = compute_hrv_features(target, samples)

    assert feature["status"] == "computed"
    assert "low_confidence_heart_rate_variability" in feature["data_quality"]["flags"]
    assert feature["source_sample_ids"] == []


def test_anomalous_sleep_provenance_is_preserved_for_traceability() -> None:
    target = dt.date(2026, 1, 20)

    feature = compute_sleep_features(
        target,
        [
            SleepSessionInput(
                start_time=dt.datetime(2026, 1, 19, 22, 0, tzinfo=dt.UTC),
                end_time=dt.datetime(2026, 1, 20, 16, 0, tzinfo=dt.UTC),
                duration_seconds=18 * 3600,
                source_sample_ids=("bad-sleep-duration",),
            )
        ],
    )

    assert "anomalous_sleep_duration" in feature["data_quality"]["flags"]
    assert feature["source_sample_ids"] == ["bad-sleep-duration"]


def test_assembler_returns_derived_daily_feature_field_shape() -> None:
    target = _target_date("high_hrv_good_sleep_low_load")

    bundle = assemble_daily_features(
        target,
        sleep_sessions=_sleep_inputs("high_hrv_good_sleep_low_load"),
        hrv_samples=_cardio_inputs("high_hrv_good_sleep_low_load", "heart_rate_variability"),
        rhr_samples=_cardio_inputs("high_hrv_good_sleep_low_load", "resting_heart_rate"),
        personal_sleep_need_hours=8.35,
        computed_at=dt.datetime(2026, 1, 25, 8, 0, tzinfo=dt.UTC),
    )

    derived_fields = bundle.to_derived_daily_feature_fields()

    assert derived_fields["feature_version"] == FEATURE_VERSION
    assert derived_fields["sleep_features"]["status"] == "computed"
    assert derived_fields["hrv_features"]["status"] == "computed"
    assert derived_fields["rhr_features"]["status"] == "computed"
    assert derived_fields["training_load_features"]["status"] == "insufficient_data"
    assert derived_fields["recovery_features"]["status"] == "computed"
    assert derived_fields["goal_features"]["status"] == "insufficient_data"
    assert derived_fields["computed_at"] == dt.datetime(2026, 1, 25, 8, 0, tzinfo=dt.UTC)
    assert derived_fields["source_sample_ids"]


@given(
    durations=st.lists(st.floats(min_value=2_000, max_value=40_000), min_size=1, max_size=8),
    qualities=st.lists(st.floats(min_value=0, max_value=1), min_size=1, max_size=8),
)
def test_computed_feature_values_never_contain_nan_or_none(
    durations: list[float],
    qualities: list[float],
) -> None:
    target = dt.date(2026, 1, 20)
    sessions = [
        SleepSessionInput(
            start_time=dt.datetime(2026, 1, 19 - index, 22, 0, tzinfo=dt.UTC),
            end_time=dt.datetime(2026, 1, 20 - index, 6, 0, tzinfo=dt.UTC),
            duration_seconds=duration,
            quality_proxy=qualities[index % len(qualities)],
            source_sample_ids=(f"sleep-{index}",),
        )
        for index, duration in enumerate(durations)
    ]

    feature = compute_sleep_features(target, sessions)

    for computed in _walk_values(feature):
        value = computed["value"]
        assert value is not None
        if isinstance(value, float):
            assert math.isfinite(value)
