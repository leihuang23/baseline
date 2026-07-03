"""Versioned API request and response contracts from PRD section 17."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from baseline_api.schemas.common import ContractModel
from baseline_api.schemas.enums import (
    AnalysisJobStatus,
    ConfidenceLevel,
    DataExportFormat,
    DataExportScope,
    DataExportStatus,
    DataScope,
    EvalQueueStatus,
    FeedbackActionTaken,
    FeedbackRating,
    MemoryUpdateStatus,
    MetricType,
    PrivacyMode,
    ReadinessState,
    RecommendationBand,
    RedactionStatus,
    SafetyStatus,
    SensitiveNotePolicy,
)
from baseline_api.schemas.recommendation import (
    DataQualityNote,
    ExternalCitation,
    PersonalEvidence,
    RecommendationAlternative,
)

Score = int


class HealthSamplePayload(ContractModel):
    source_sample_id: str = Field(min_length=1)
    sample_type: MetricType
    start_time: dt.datetime
    end_time: dt.datetime | None = None
    value: float
    unit: str = Field(min_length=1)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class HealthSyncRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    client_sync_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    samples: list[HealthSamplePayload]
    last_anchor: str | None = None
    consent_version: str = Field(min_length=1)


class DataQualitySummary(ContractModel):
    status: Literal["ok", "degraded", "insufficient"]
    notes: list[DataQualityNote] = Field(default_factory=list)


class HealthSyncResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    sync_id: UUID
    accepted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    next_anchor: str
    data_quality_summary: DataQualitySummary


class DailyCheckInFlags(ContractModel):
    alcohol: bool = False
    illness: bool = False
    injury: bool = False
    travel: bool = False


class DailyCheckInRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    date: dt.date
    energy_score: Score | None = Field(default=None, ge=1, le=10)
    mood_score: Score | None = Field(default=None, ge=1, le=10)
    soreness_score: Score | None = Field(default=None, ge=1, le=10)
    stress_score: Score | None = Field(default=None, ge=1, le=10)
    perceived_recovery_score: Score | None = Field(default=None, ge=1, le=10)
    food_quality_score: Score | None = Field(default=None, ge=1, le=10)
    flags: DailyCheckInFlags = Field(default_factory=DailyCheckInFlags)
    structured_notes: dict[str, Any] = Field(default_factory=dict)
    free_text_note: str | None = None
    sensitive_note_policy: SensitiveNotePolicy


class DailyCheckInResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    checkin_id: UUID
    accepted_fields: list[str]
    redaction_status: RedactionStatus
    analysis_job_id: UUID | None = None


class DailyAnalysisRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    date: dt.date
    force_recompute: bool = False
    include_external_knowledge: bool = False
    privacy_mode: PrivacyMode


class DailyAnalysisResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    analysis_job_id: UUID
    status: AnalysisJobStatus
    estimated_completion_seconds: int = Field(ge=0)


class DataFreshness(ContractModel):
    latest_sample_at: dt.datetime | None = None
    latest_checkin_date: dt.date | None = None
    stale_sources: list[str] = Field(default_factory=list)


class CandidateOption(ContractModel):
    label: str = Field(min_length=1)
    recommendation_band: RecommendationBand
    rationale: str = Field(min_length=1)


class GoalTradeoff(ContractModel):
    goal: str = Field(min_length=1)
    tradeoff: str = Field(min_length=1)


class DailyBriefingResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    date: dt.date
    readiness_state: ReadinessState
    confidence: ConfidenceLevel
    data_freshness: DataFreshness
    evidence: list[PersonalEvidence] = Field(min_length=1)
    recommendation_band: RecommendationBand
    candidate_options: list[CandidateOption] = Field(default_factory=list)
    goal_tradeoffs: list[GoalTradeoff] = Field(default_factory=list)
    uncertainty: list[str] = Field(min_length=1)
    safety_notes: list[str] = Field(min_length=1)
    trace_id: UUID
    generated_at: dt.datetime


class AssistantQueryRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    question: str = Field(min_length=1)
    date_context: dt.date | None = None
    allowed_data_scope: list[DataScope] = Field(min_length=1)
    include_external_knowledge: bool = False
    privacy_mode: PrivacyMode


class AssistantQueryResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    answer: str = Field(min_length=1)
    personal_evidence: list[PersonalEvidence] = Field(min_length=1)
    external_sources: list[ExternalCitation] = Field(default_factory=list)
    confidence: ConfidenceLevel
    uncertainty: list[str] = Field(min_length=1)
    safety_status: SafetyStatus
    trace_id: UUID


class RecommendationFeedbackRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    rating: FeedbackRating
    action_taken: FeedbackActionTaken
    reason: str | None = None
    outcome_notes: str | None = None


class RecommendationFeedbackResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    feedback_id: UUID
    memory_update_status: MemoryUpdateStatus
    eval_queue_status: EvalQueueStatus


class DataExportRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    export_scope: DataExportScope
    format: DataExportFormat
    include_raw_data: bool = False
    include_model_traces: bool = False


class DataExportResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    export_job_id: UUID
    status: DataExportStatus
    expires_at: dt.datetime


__all__ = [
    "AssistantQueryRequest",
    "AssistantQueryResponse",
    "CandidateOption",
    "DailyAnalysisRequest",
    "DailyAnalysisResponse",
    "DailyBriefingResponse",
    "DailyCheckInRequest",
    "DailyCheckInResponse",
    "DataExportRequest",
    "DataExportResponse",
    "DataFreshness",
    "DataQualitySummary",
    "GoalTradeoff",
    "HealthSamplePayload",
    "HealthSyncRequest",
    "HealthSyncResponse",
    "RecommendationAlternative",
    "RecommendationFeedbackRequest",
    "RecommendationFeedbackResponse",
]
