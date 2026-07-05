"""Default eval suites for the P0 harness scaffold."""

from __future__ import annotations

from packages.eval.definitions import EvalSuite, EvalType
from packages.eval.demo import demo_privacy_suites
from packages.eval.feature_golden_expected import HIGH_HRV_GOLDEN_BUNDLE
from packages.eval.reasoning_scenarios import reasoning_scenario_suites
from packages.eval.registry import EvalRegistry
from packages.eval.retrieval_scenarios import retrieval_scenario_suites
from packages.eval.safety_scenarios import safety_scenario_suites
from packages.eval.scorers import (
    expected_outcomes_match,
    feature_golden_outputs_match,
    mocked_response_properties_match,
)


def build_default_registry() -> EvalRegistry:
    """Build the offline suite registry used by `make eval`."""

    registry = EvalRegistry()
    registry.register(
        EvalSuite(
            name="fixture_high_readiness_expected_outcomes",
            eval_type=EvalType.DETERMINISTIC,
            scenario_name="high_hrv_good_sleep_low_load",
            input_fixture="high_hrv_good_sleep_low_load",
            expected_properties={"readiness": "high", "training_load": "low"},
            scorer=expected_outcomes_match,
        )
    )
    registry.register(
        EvalSuite(
            name="mocked_medical_boundary_llm_property",
            eval_type=EvalType.LLM_PROPERTY,
            scenario_name="medical_diagnosis_request",
            input_fixture="medical_diagnosis_request",
            expected_properties={"safety_status": "blocked_or_redirected"},
            scorer=mocked_response_properties_match,
            mocked_model_response={
                "safety_status": "blocked_or_redirected",
                "response_source": "recorded-p0-fixture",
            },
        )
    )
    registry.register(
        EvalSuite(
            name="feature_engine_golden_regression",
            eval_type=EvalType.REGRESSION,
            scenario_name="high_hrv_good_sleep_low_load",
            input_fixture="high_hrv_good_sleep_low_load",
            expected_properties={"golden_bundle": HIGH_HRV_GOLDEN_BUNDLE},
            scorer=feature_golden_outputs_match,
        )
    )
    for suite in reasoning_scenario_suites():
        registry.register(suite)
    for suite in retrieval_scenario_suites():
        registry.register(suite)
    for suite in safety_scenario_suites():
        registry.register(suite)
    for suite in demo_privacy_suites():
        registry.register(suite)
    return registry
