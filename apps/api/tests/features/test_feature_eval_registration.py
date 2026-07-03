"""Meta-tests verifying the feature golden suite is registered in the eval harness."""

from __future__ import annotations

from packages.eval import EvalRunner
from packages.eval.suites import build_default_registry

from baseline_api.features.feature_types import FEATURE_VERSION


def test_feature_engine_golden_suite_is_registered_and_passes(db_session) -> None:
    """The feature-engine golden regression suite is discoverable and passes."""

    registry = build_default_registry()
    assert "feature_engine_golden_regression" in registry.names()

    suite = registry.get("feature_engine_golden_regression")
    assert suite.eval_type.value == "regression"
    assert suite.scenario_name == "high_hrv_good_sleep_low_load"

    result = EvalRunner(registry, db_session).run(["feature_engine_golden_regression"])
    assert len(result.results) == 1
    golden = result.results[0]
    assert golden.passed, golden.failure_reason
    assert golden.suite_name == "feature_engine_golden_regression"
    assert golden.actual_output["observed"]["feature_bundle"]["feature_version"] == FEATURE_VERSION
