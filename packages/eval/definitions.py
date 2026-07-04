"""Core types for scenario-driven eval suites."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from packages.fixtures.models import FixtureDataset


class EvalType(StrEnum):
    """Supported eval categories."""

    DETERMINISTIC = "deterministic"
    LLM_PROPERTY = "llm_property"
    REASONING = "reasoning"
    RETRIEVAL = "retrieval"
    SAFETY = "safety"
    PRIVACY = "privacy"
    REGRESSION = "regression"


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Pass/fail output from a suite scorer."""

    passed: bool
    observed: dict[str, Any] = field(default_factory=dict)
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.passed and not self.failure_reason:
            raise ValueError("Failing score results must include failure_reason")


@dataclass(frozen=True, slots=True)
class EvalContext:
    """Runtime context passed to scorers."""

    suite_name: str
    eval_type: EvalType
    scenario_name: str
    fixture: FixtureDataset
    expected_properties: Mapping[str, Any]
    mocked_model_response: Mapping[str, Any] | None = None


Scorer = Callable[[EvalContext], ScoreResult]


@dataclass(frozen=True, slots=True)
class EvalSuite:
    """A registered evaluation suite bound to a synthetic fixture."""

    name: str
    eval_type: EvalType
    scenario_name: str
    input_fixture: str
    expected_properties: Mapping[str, Any]
    scorer: Scorer
    mocked_model_response: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.eval_type is EvalType.LLM_PROPERTY and self.mocked_model_response is None:
            raise ValueError("LLM eval suites must use a mocked or recorded model response")


@dataclass(frozen=True, slots=True)
class EvalResult:
    """Persisted and reportable result for one suite execution."""

    suite_name: str
    eval_type: EvalType
    scenario_name: str
    input_fixture: str
    expected_properties: Mapping[str, Any]
    actual_output: Mapping[str, Any]
    passed: bool
    failure_reason: str | None
    evaluated_at: dt.datetime
    evaluation_case_id: UUID

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "eval_type": self.eval_type.value,
            "scenario_name": self.scenario_name,
            "input_fixture": self.input_fixture,
            "expected_properties": dict(self.expected_properties),
            "actual_output": dict(self.actual_output),
            "passed": self.passed,
            "failure_reason": self.failure_reason,
            "evaluated_at": self.evaluated_at.isoformat(),
            "evaluation_case_id": str(self.evaluation_case_id),
        }
