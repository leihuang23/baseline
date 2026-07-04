"""Recommendation output contract from PRD section 18."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, HttpUrl

from baseline_api.schemas.common import ContractModel
from baseline_api.schemas.enums import (
    ConfidenceLevel,
    DataQualitySeverity,
    ReadinessState,
    RecommendationBand,
    SafetyStatus,
)


class PersonalEvidence(ContractModel):
    metric: str = Field(min_length=1)
    value: str | int | float | bool = Field()
    interpretation: str = Field(min_length=1)
    source: str | None = None


class MemoryObservation(ContractModel):
    observation: str = Field(min_length=1)
    relevance: str = Field(min_length=1)
    period: str | None = None


class ExternalCitation(ContractModel):
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    url: HttpUrl | None = None
    cited_claim: str = Field(min_length=1)


class DataQualityNote(ContractModel):
    metric: str | None = None
    note: str = Field(min_length=1)
    severity: DataQualitySeverity = DataQualitySeverity.info


class RecommendationSummary(ContractModel):
    primary: str = Field(min_length=1)
    avoid: str | None = None


class RecommendationAlternative(ContractModel):
    label: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class FollowUpPrompt(ContractModel):
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RecommendationContract(ContractModel):
    schema_version: Literal["v1"] = "v1"
    readiness_state: ReadinessState
    recommendation_band: RecommendationBand
    confidence: ConfidenceLevel
    personal_evidence: list[PersonalEvidence] = Field(min_length=1)
    memory_observations: list[MemoryObservation] = Field(default_factory=list)
    external_citations: list[ExternalCitation] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    recommendation: RecommendationSummary
    uncertainty: list[str] = Field(min_length=1)
    data_quality_notes: list[DataQualityNote] = Field(default_factory=list)
    safety_status: SafetyStatus
    safety_note: str = Field(min_length=1)
    safety_result: dict[str, Any] = Field(min_length=1)
    alternatives: list[RecommendationAlternative] = Field(default_factory=list)
    follow_up: FollowUpPrompt | None = None
