"""DB-free regression tests for assistant query planning guardrails."""

from baseline_api.assistant.service import _confidence_for_single_count, _plan_query
from baseline_api.schemas.enums import ConfidenceLevel


def test_unsupported_question_does_not_default_to_recovery() -> None:
    plan = _plan_query("How did hydration change this month?")

    assert plan.intent == "compare_periods"
    assert plan.metric is None


def test_single_recent_datapoint_is_low_confidence() -> None:
    assert _confidence_for_single_count(1) is ConfidenceLevel.low
