"""Reusable deterministic scorers for eval suites."""

from __future__ import annotations

from typing import Any

from packages.eval.definitions import EvalContext, ScoreResult


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
