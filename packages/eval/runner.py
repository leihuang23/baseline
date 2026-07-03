"""Evaluation runner and CI gate policy."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from baseline_api.db.models.evaluation import EvaluationCase
from baseline_api.db.repositories.evaluation import EvaluationCaseRepository
from packages.eval.definitions import EvalContext, EvalResult, EvalSuite, EvalType
from packages.eval.registry import EvalRegistry
from packages.fixtures import get_scenario
from packages.fixtures.models import fixture_to_dict

GATED_FAILURE_TYPES = frozenset({EvalType.REGRESSION, EvalType.SAFETY})


@dataclass(frozen=True, slots=True)
class EvalRunResult:
    """Aggregate result for one eval harness run."""

    evaluated_at: dt.datetime
    results: list[EvalResult]

    def to_report_dict(self) -> dict[str, Any]:
        failures = [result for result in self.results if not result.passed]
        by_type: dict[str, dict[str, int]] = {}
        for result in self.results:
            summary = by_type.setdefault(
                result.eval_type.value, {"total": 0, "passed": 0, "failed": 0}
            )
            summary["total"] += 1
            if result.passed:
                summary["passed"] += 1
            else:
                summary["failed"] += 1

        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "summary": {
                "total": len(self.results),
                "passed": sum(1 for result in self.results if result.passed),
                "failed": len(failures),
                "by_type": by_type,
            },
            "gate_failed": gate_failed(self),
            "gated_failure_types": sorted(eval_type.value for eval_type in GATED_FAILURE_TYPES),
            "failures": [
                {
                    "suite_name": result.suite_name,
                    "eval_type": result.eval_type.value,
                    "scenario_name": result.scenario_name,
                    "failure_reason": result.failure_reason,
                }
                for result in failures
            ],
            "results": [result.to_report_dict() for result in self.results],
        }


class EvalRunner:
    """Run registered suites, persist `EvaluationCase` rows, and return report data."""

    def __init__(self, registry: EvalRegistry, session: Session) -> None:
        self.registry = registry
        self.repository = EvaluationCaseRepository(session)

    def run(self, suite_names: Iterable[str] | None = None) -> EvalRunResult:
        evaluated_at = dt.datetime.now(dt.UTC)
        results = [
            self._run_suite(suite, evaluated_at) for suite in self.registry.selected(suite_names)
        ]
        return EvalRunResult(evaluated_at=evaluated_at, results=results)

    def _run_suite(self, suite: EvalSuite, evaluated_at: dt.datetime) -> EvalResult:
        fixture = get_scenario(suite.input_fixture)
        context = EvalContext(
            suite_name=suite.name,
            eval_type=suite.eval_type,
            scenario_name=suite.scenario_name,
            fixture=fixture,
            expected_properties=suite.expected_properties,
            mocked_model_response=suite.mocked_model_response,
        )
        score = suite.scorer(context)
        actual_output = _actual_output(suite, score.observed, score.failure_reason)
        case = self.repository.create(
            EvaluationCase(
                scenario_name=suite.scenario_name,
                input_fixture=fixture_to_dict(fixture),
                expected_properties=dict(suite.expected_properties),
                actual_output=actual_output,
                pass_fail=score.passed,
                failure_reason=score.failure_reason,
                evaluated_at=evaluated_at,
            )
        )

        return EvalResult(
            suite_name=suite.name,
            eval_type=suite.eval_type,
            scenario_name=suite.scenario_name,
            input_fixture=suite.input_fixture,
            expected_properties=suite.expected_properties,
            actual_output=actual_output,
            passed=score.passed,
            failure_reason=score.failure_reason,
            evaluated_at=evaluated_at,
            evaluation_case_id=case.id,
        )


def gate_failed(run_result: EvalRunResult) -> bool:
    """Return whether CI should fail for the completed run."""

    return any(
        result.eval_type in GATED_FAILURE_TYPES and not result.passed
        for result in run_result.results
    )


def _actual_output(
    suite: EvalSuite,
    observed: Mapping[str, Any],
    failure_reason: str | None,
) -> dict[str, Any]:
    output = {
        "suite_name": suite.name,
        "eval_type": suite.eval_type.value,
        "observed": dict(observed),
    }
    if suite.mocked_model_response is not None:
        output["mocked_model_response"] = dict(suite.mocked_model_response)
    if failure_reason is not None:
        output["failure_reason"] = failure_reason
    return output
