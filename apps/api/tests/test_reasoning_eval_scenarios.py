"""Meta-tests for reasoning golden-scenario evaluations."""

from packages.eval import EvalRunner, EvalType
from packages.eval.reasoning_scenarios import (
    REASONING_NAMED_SCENARIO_CASES,
    REASONING_SCENARIO_CASES,
    REASONING_SUITE_PREFIX,
)
from packages.eval.suites import build_default_registry
from packages.fixtures import GOLDEN_SCENARIO_NAMES


def _reasoning_suites():
    registry = build_default_registry()
    return registry, [
        registry.get(name) for name in registry.names() if name.startswith(REASONING_SUITE_PREFIX)
    ]


def test_reasoning_golden_scenarios_are_registered() -> None:
    """The reasoning suite covers at least 30 scenarios, including the 10 canonical names."""

    _, reasoning_suites = _reasoning_suites()
    registered_scenario_names = {suite.scenario_name for suite in reasoning_suites}

    assert len(REASONING_SCENARIO_CASES) >= 30
    assert len(reasoning_suites) >= 30
    assert set(GOLDEN_SCENARIO_NAMES) <= registered_scenario_names
    assert {case.scenario_name for case in REASONING_NAMED_SCENARIO_CASES} == set(
        GOLDEN_SCENARIO_NAMES
    )
    assert all(suite.eval_type is EvalType.REASONING for suite in reasoning_suites)


def test_reasoning_golden_scenarios_pass_through_harness(db_session) -> None:
    """The registered reasoning scenarios pass when persisted by the eval harness."""

    registry, reasoning_suites = _reasoning_suites()

    result = EvalRunner(
        registry,
        db_session,
    ).run(suite.name for suite in reasoning_suites)

    assert len(result.results) == len(reasoning_suites)
    assert all(eval_result.passed for eval_result in result.results), [
        (eval_result.suite_name, eval_result.failure_reason)
        for eval_result in result.results
        if not eval_result.passed
    ]
