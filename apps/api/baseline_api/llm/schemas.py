"""Structured LLM input and output contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, HttpUrl

from baseline_api.schemas.common import ContractModel

SCHEMA_VERSION: Literal["llm_explanation_v1"] = "llm_explanation_v1"


class TaskType(StrEnum):
    """Model routing categories."""

    classification = "classification"
    summarization = "summarization"
    simple_explanation = "simple_explanation"
    complex_longitudinal = "complex_longitudinal"
    planning = "planning"


class ExternalCitation(ContractModel):
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    url: HttpUrl | None = None
    cited_claim: str = Field(min_length=1)


class LLMExplanationOutput(ContractModel):
    """Schema-valid user-facing explanation bounded by deterministic inputs."""

    schema_version: Literal["llm_explanation_v1"] = "llm_explanation_v1"
    summary: str = Field(min_length=1)
    rationale: list[str] = Field(min_length=1)
    uncertainty: list[str] = Field(min_length=1)
    personal_evidence_refs: list[str] = Field(min_length=1)
    external_citations: list[ExternalCitation] = Field(default_factory=list)
    safety_boundary_acknowledged: Literal[True]
    no_diagnosis_or_treatment_claims: Literal[True]


class PromptInputs(ContractModel):
    """Minimized context allowed into the LLM prompt."""

    task_type: TaskType = TaskType.simple_explanation
    request_text: str | None = None
    deterministic_assessment: dict[str, Any]
    derived_features: dict[str, Any] = Field(default_factory=dict)
    retrieved_evidence: list[dict[str, Any]] = Field(default_factory=list)
    external_knowledge: list[dict[str, Any]] = Field(default_factory=list)
    raw_samples: list[dict[str, Any]] = Field(default_factory=list)
    raw_notes: list[str] = Field(default_factory=list)
