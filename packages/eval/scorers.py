"""Reusable deterministic scorers for eval suites."""

from __future__ import annotations

import datetime as dt
from typing import Any

from baseline_api.features.assembler import assemble_daily_features
from packages.eval.definitions import EvalContext, ScoreResult
from packages.eval.feature_adapter import (
    hrv_inputs,
    rhr_inputs,
    sleep_inputs,
    target_date,
    vo2_inputs,
    workout_inputs,
)


def _jsonify_datetime(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonify_datetime(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify_datetime(v) for v in value]
    return value


def expected_outcomes_match(context: EvalContext) -> ScoreResult:
    """Compare expected properties against the fixture's expected outcomes."""

    observed: dict[str, Any] = {
        key: context.fixture.expected_outcomes.get(key) for key in context.expected_properties
    }
    mismatches = {
        key: {"expected": expected, "observed": observed[key]}
        for key, expected in context.expected_properties.items()
        if observed[key] != expected
    }
    if mismatches:
        return ScoreResult(
            passed=False,
            observed={"expected_outcomes": observed, "mismatches": mismatches},
            failure_reason=f"Expected outcome mismatches: {sorted(mismatches)}",
        )
    return ScoreResult(passed=True, observed={"expected_outcomes": observed})


def mocked_response_properties_match(context: EvalContext) -> ScoreResult:
    """Compare expected properties against an offline model response."""

    response = dict(context.mocked_model_response or {})
    observed = {key: response.get(key) for key in context.expected_properties}
    mismatches = {
        key: {"expected": expected, "observed": observed[key]}
        for key, expected in context.expected_properties.items()
        if observed[key] != expected
    }
    if mismatches:
        return ScoreResult(
            passed=False,
            observed={"model_response": observed, "mismatches": mismatches},
            failure_reason=f"Mocked model response mismatches: {sorted(mismatches)}",
        )
    return ScoreResult(passed=True, observed={"model_response": observed})


def feature_golden_outputs_match(context: EvalContext) -> ScoreResult:
    """Compare the assembled daily feature bundle against a versioned golden output."""

    fixture = context.fixture
    date = target_date(fixture)
    bundle = assemble_daily_features(
        date,
        sleep_sessions=sleep_inputs(fixture),
        hrv_samples=hrv_inputs(fixture),
        rhr_samples=rhr_inputs(fixture),
        workouts=workout_inputs(fixture),
        vo2_samples=vo2_inputs(fixture),
        computed_at=dt.datetime(2026, 1, 20, 8, 0, 0, tzinfo=dt.UTC),
    )
    actual = _jsonify_datetime(bundle.to_derived_daily_feature_fields())
    expected = context.expected_properties.get("golden_bundle")
    if actual == expected:
        return ScoreResult(passed=True, observed={"feature_bundle": actual})
    return ScoreResult(
        passed=False,
        observed={"feature_bundle": actual, "expected_bundle": expected},
        failure_reason="Feature engine golden bundle mismatch; formula or fixture drift detected.",
    )
