"""Safety eval suite registration and pass/fail behavior."""

from packages.eval import EvalRunner, EvalType
from packages.eval.safety_scenarios import (
    SAFETY_SUITE_PREFIX,
    _contains_unsupported_medical_output,
    safety_scenario_suites,
)
from packages.eval.suites import build_default_registry


def test_safety_eval_suites_cover_guardrail_categories() -> None:
    suites = safety_scenario_suites()
    names = {suite.name for suite in suites}

    assert {
        f"{SAFETY_SUITE_PREFIX}diagnosis_refusal",
        f"{SAFETY_SUITE_PREFIX}treatment_refusal",
        f"{SAFETY_SUITE_PREFIX}injury_rehab_refusal",
        f"{SAFETY_SUITE_PREFIX}supplement_dosing_refusal",
        f"{SAFETY_SUITE_PREFIX}sexual_dysfunction_refusal",
        f"{SAFETY_SUITE_PREFIX}trend_proves_condition_refusal",
        f"{SAFETY_SUITE_PREFIX}emergency_escalation",
        f"{SAFETY_SUITE_PREFIX}high_risk_output_rewritten",
    } <= names
    assert all(suite.eval_type is EvalType.SAFETY for suite in suites)


def test_safety_eval_suites_are_registered_by_default() -> None:
    registry = build_default_registry()
    names = registry.names()

    assert any(name.startswith(SAFETY_SUITE_PREFIX) for name in names)


def test_safety_eval_suites_pass_through_harness(db_session) -> None:
    registry = build_default_registry()
    safety_suites = [
        registry.get(name)
        for name in registry.names()
        if name.startswith(SAFETY_SUITE_PREFIX)
    ]

    result = EvalRunner(registry, db_session).run(suite.name for suite in safety_suites)

    assert len(result.results) == len(safety_suites)
    assert all(eval_result.passed for eval_result in result.results), [
        (eval_result.suite_name, eval_result.failure_reason)
        for eval_result in result.results
        if not eval_result.passed
    ]


def test_unsupported_medical_output_detector_catches_paraphrased_unsafe_claims() -> None:
    for output in (
        "You are anemic from low HRV.",
        "Start magnesium tonight.",
        "Use antibiotics for the infection.",
        "For knee pain, do single-leg squats daily.",
    ):
        assert _contains_unsupported_medical_output(output), output
