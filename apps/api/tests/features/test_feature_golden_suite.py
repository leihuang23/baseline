"""Exact-output golden tests for the deterministic feature engine.

These tests compare live feature calculations against versioned expected outputs.
If a formula changes without updating fixtures or `FEATURE_VERSION`, they fail.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from baseline_api.features.assembler import assemble_daily_features
from baseline_api.features.cardio import compute_hrv_features, compute_rhr_features
from baseline_api.features.feature_types import FEATURE_VERSION
from baseline_api.features.sleep import compute_sleep_features
from baseline_api.features.training_load import (
    compute_training_load_features,
    compute_vo2_features,
)

from .golden_fixtures import FIXTURES, load_expected_outputs

EXPECTED = load_expected_outputs()

SECTIONS = ["sleep", "hrv", "rhr", "training_load", "vo2"]


def _normalize(value: Any) -> Any:
    """Make feature dicts JSON-comparable by serializing datetimes."""

    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalize(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_normalize(subvalue) for subvalue in value]
    return value


def _compute_section(
    fixture_name: str,
    section: str,
) -> dict[str, Any]:
    fixture = FIXTURES[fixture_name]
    if section == "sleep":
        return compute_sleep_features(
            fixture.target_date,
            fixture.sleep_sessions,
            personal_sleep_need_hours=fixture.personal_sleep_need_hours,
        )
    if section == "hrv":
        return compute_hrv_features(fixture.target_date, fixture.hrv_samples)
    if section == "rhr":
        return compute_rhr_features(fixture.target_date, fixture.rhr_samples)
    if section == "training_load":
        return compute_training_load_features(fixture.target_date, fixture.workouts)
    if section == "vo2":
        return compute_vo2_features(fixture.target_date, fixture.vo2_samples)
    raise ValueError(f"Unknown section: {section}")


def _assemble_bundle(fixture_name: str) -> dict[str, Any]:
    fixture = FIXTURES[fixture_name]
    bundle = assemble_daily_features(
        fixture.target_date,
        sleep_sessions=fixture.sleep_sessions,
        hrv_samples=fixture.hrv_samples,
        rhr_samples=fixture.rhr_samples,
        workouts=fixture.workouts,
        vo2_samples=fixture.vo2_samples,
        personal_sleep_need_hours=fixture.personal_sleep_need_hours,
        computed_at=dt.datetime(2026, 1, 20, 8, 0, 0, tzinfo=dt.UTC),
    )
    return _normalize(bundle.to_derived_daily_feature_fields())


@pytest.mark.parametrize("fixture_name", list(FIXTURES))
@pytest.mark.parametrize("section", SECTIONS)
def test_feature_section_matches_golden_fixture(
    fixture_name: str,
    section: str,
) -> None:
    """Every feature section must exactly match its versioned golden output."""

    actual = _compute_section(fixture_name, section)
    expected = EXPECTED[fixture_name][section]
    assert _normalize(actual) == expected


@pytest.mark.parametrize("fixture_name", list(FIXTURES))
def test_assembled_bundle_matches_golden_fixture(fixture_name: str) -> None:
    """The daily bundle assembler must exactly match its versioned golden output."""

    actual = _assemble_bundle(fixture_name)
    expected = EXPECTED[fixture_name]["bundle"]
    assert actual == expected


@pytest.mark.parametrize("fixture_name", list(FIXTURES))
def test_feature_versions_match_current_release(fixture_name: str) -> None:
    """Golden fixtures and live output must agree on the current feature version."""

    for section in SECTIONS:
        actual = _compute_section(fixture_name, section)
        expected = EXPECTED[fixture_name][section]
        assert actual["feature_version"] == FEATURE_VERSION
        assert actual["calculation_metadata"]["formula_version"] == FEATURE_VERSION
        assert expected["feature_version"] == FEATURE_VERSION
        assert expected["calculation_metadata"]["formula_version"] == FEATURE_VERSION

    bundle = _assemble_bundle(fixture_name)
    expected_bundle = EXPECTED[fixture_name]["bundle"]
    assert bundle["feature_version"] == FEATURE_VERSION
    assert expected_bundle["feature_version"] == FEATURE_VERSION


MISSING_FIXTURES = [
    "missing_hrv",
    "missing_sleep",
    "missing_rhr",
    "missing_training_load",
    "missing_vo2",
]


@pytest.mark.parametrize("fixture_name", MISSING_FIXTURES)
def test_missing_inputs_yield_insufficient_data_or_flags(fixture_name: str) -> None:
    """Missing or degraded inputs must never produce fabricated numeric values."""

    fixture = FIXTURES[fixture_name]
    if fixture_name == "missing_hrv":
        feature = compute_hrv_features(fixture.target_date, fixture.hrv_samples)
        assert feature["status"] == "insufficient_data"
        assert feature["values"]["today_ms"]["status"] == "insufficient_data"
        assert "missing_heart_rate_variability" in feature["data_quality"]["flags"]
    elif fixture_name == "missing_sleep":
        feature = compute_sleep_features(
            fixture.target_date,
            fixture.sleep_sessions,
            personal_sleep_need_hours=fixture.personal_sleep_need_hours,
        )
        assert feature["status"] == "insufficient_data"
        assert feature["values"]["duration_hours"]["status"] == "insufficient_data"
        assert "missing_sleep" in feature["data_quality"]["flags"]
    elif fixture_name == "missing_rhr":
        feature = compute_rhr_features(fixture.target_date, fixture.rhr_samples)
        assert feature["status"] == "insufficient_data"
        assert feature["values"]["today_bpm"]["status"] == "insufficient_data"
        assert "missing_resting_heart_rate" in feature["data_quality"]["flags"]
    elif fixture_name == "missing_training_load":
        feature = compute_training_load_features(fixture.target_date, fixture.workouts)
        assert feature["status"] == "insufficient_data"
        assert feature["values"]["acute_load_units"]["status"] == "insufficient_data"
        assert "baseline_not_established_acute_load" in feature["data_quality"]["flags"]
    elif fixture_name == "missing_vo2":
        feature = compute_vo2_features(fixture.target_date, fixture.vo2_samples)
        assert feature["status"] == "insufficient_data"
        assert feature["values"]["trend_slope_per_week"]["status"] == "insufficient_data"
        assert "missing_vo2_max" in feature["data_quality"]["flags"]


@pytest.mark.parametrize(
    ("fixture_name", "expected_flags"),
    [
        ("stale_data", ["missing_sleep", "stale_sleep"]),
        ("anomalous_spike", ["anomalous_sleep_duration", "anomalous_heart_rate_variability"]),
        (
            "conflicting_samples",
            ["conflicting_sleep_sessions", "conflicting_heart_rate_variability"],
        ),
    ],
)
def test_degraded_inputs_raise_expected_flags(
    fixture_name: str,
    expected_flags: list[str],
) -> None:
    """Stale, anomalous, and conflicting inputs surface expected flags."""

    bundle = _assemble_bundle(fixture_name)
    flags = bundle["data_quality"]["flags"]
    for flag in expected_flags:
        assert flag in flags or any(flag in f for f in flags)


def test_high_density_training_surface_muscle_group_density() -> None:
    """High-density training weeks expose repeated muscle-group load."""

    bundle = _assemble_bundle("high_density_training")
    muscle_density = bundle["training_load_features"]["values"]["density_by_muscle_group"]["value"]
    assert muscle_density["lower_body"]["value"] == 4


def test_vo2_improving_and_recovery_declining_are_distinguishable() -> None:
    """The VO2 trend and recovery confidence move in opposite directions as expected."""

    fixture = FIXTURES["vo2_improving_recovery_declining"]
    vo2 = compute_vo2_features(fixture.target_date, fixture.vo2_samples)
    bundle = _assemble_bundle("vo2_improving_recovery_declining")

    assert vo2["values"]["trend_direction"]["value"] == "improving"
    assert bundle["recovery_features"]["values"]["level"]["value"] in {"low", "medium"}


def test_goal_features_wrap_vo2_and_are_exact() -> None:
    """Goal features are deterministic wrappers around VO2 trend output."""

    bundle = _assemble_bundle("normal_day")
    expected = EXPECTED["normal_day"]["goal"]
    assert bundle["goal_features"] == expected
