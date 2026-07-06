"""Retrieval eval suite registration and pass behavior."""

from packages.eval import EvalRunner, EvalType
from packages.eval.retrieval_scenarios import (
    RETRIEVAL_SUITE_PREFIX,
    retrieval_scenario_suites,
)
from packages.eval.runner import GATED_FAILURE_TYPES
from packages.eval.suites import build_default_registry


def test_retrieval_eval_suites_are_registered_and_gated() -> None:
    registry = build_default_registry()
    names = registry.names()

    assert any(name.startswith(RETRIEVAL_SUITE_PREFIX) for name in names)
    assert EvalType.RETRIEVAL in GATED_FAILURE_TYPES


def test_retrieval_eval_suites_cover_citation_accuracy_and_separation() -> None:
    suites = retrieval_scenario_suites()

    assert {suite.name for suite in suites} == {
        f"{RETRIEVAL_SUITE_PREFIX}external_corpus_relevance_citations",
        f"{RETRIEVAL_SUITE_PREFIX}personal_evidence_separation",
        f"{RETRIEVAL_SUITE_PREFIX}disabled_external_knowledge",
        f"{RETRIEVAL_SUITE_PREFIX}unsupported_medical_claim_suppressed",
    }
    assert all(suite.eval_type is EvalType.RETRIEVAL for suite in suites)
    assert any("citation_accuracy_min" in suite.expected_properties for suite in suites)
    assert any("separate_personal_and_external" in suite.expected_properties for suite in suites)


def test_retrieval_eval_suites_pass_through_harness(db_session) -> None:
    registry = build_default_registry()
    retrieval_suites = [
        registry.get(name) for name in registry.names() if name.startswith(RETRIEVAL_SUITE_PREFIX)
    ]

    result = EvalRunner(registry, db_session).run(suite.name for suite in retrieval_suites)

    assert len(result.results) == len(retrieval_suites)
    assert all(eval_result.passed for eval_result in result.results), [
        (eval_result.suite_name, eval_result.failure_reason)
        for eval_result in result.results
        if not eval_result.passed
    ]
