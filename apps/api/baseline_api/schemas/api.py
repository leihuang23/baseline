"""Versioned API request and response contracts from PRD section 17."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

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
    GoalCategory,
    GoalTimeHorizon,
    HealthConsentCategory,
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
    FollowUpPrompt,
    MemoryObservation,
    PersonalEvidence,
    RecommendationAlternative,
    RecommendationSummary,
)

Score = int
StructuredNoteValue = bool | int | float | str | None
CLINICAL_GOAL_DETAIL_KEYS = {
    "diagnosis",
    "dosage",
    "lab_result",
    "medication",
    "prescription",
    "symptom",
    "treatment",
}
CLINICAL_GOAL_DETAIL_TERMS = (
    "diagnosis",
    "diagnosed",
    "dosage",
    "dose",
    "medication",
    "prescription",
    "symptom",
    "treatment",
    "lab result",
    "blood test",
    "injury rehab",
    "sexual dysfunction",
    "erectile dysfunction",
)
FEEDBACK_TEXT_MAX_LENGTH = 240


def _contains_clinical_goal_detail(value: str) -> bool:
    return any(term in value.lower() for term in CLINICAL_GOAL_DETAIL_TERMS)


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
    caffeine_notes: str | None = Field(default=None, max_length=80)
    illness: bool = False
    injury: bool = False
    travel: bool = False

    @field_validator("caffeine_notes")
    @classmethod
    def validate_caffeine_notes(cls, value: str | None) -> str | None:
        if value is not None and "\n" in value:
            raise ValueError("Caffeine notes must be short high-level indicators.")
        return value


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
    structured_notes: dict[str, StructuredNoteValue] = Field(default_factory=dict)
    free_text_note: str | None = None
    sensitive_note_policy: SensitiveNotePolicy = SensitiveNotePolicy.exclude_from_external_llm

    @field_validator("structured_notes")
    @classmethod
    def validate_structured_notes(
        cls,
        value: dict[str, StructuredNoteValue],
    ) -> dict[str, StructuredNoteValue]:
        for key, note_value in value.items():
            if not key.strip() or len(key) > 64:
                raise ValueError("Structured note keys must be non-empty and at most 64 chars.")
            if isinstance(note_value, str) and ("\n" in note_value or len(note_value) > 80):
                raise ValueError("Structured note text values must be short high-level indicators.")
        return value


class DailyCheckInResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    checkin_id: UUID
    accepted_fields: list[str]
    redaction_status: RedactionStatus
    analysis_job_id: UUID | None = None


class DailyCheckInDetailResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    checkin_id: UUID
    request: DailyCheckInRequest
    has_free_text_note: bool = False


class GoalRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    category: GoalCategory
    priority: int = Field(ge=1, le=5)
    time_horizon: GoalTimeHorizon
    success_metric: str = Field(min_length=1, max_length=160)
    constraints: dict[str, str] = Field(default_factory=dict)

    @field_validator("success_metric")
    @classmethod
    def validate_success_metric(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Success metrics must be non-empty.")
        if "\n" in value:
            raise ValueError("Success metrics must be short high-level indicators.")
        if _contains_clinical_goal_detail(normalized):
            raise ValueError("Goal success metrics must stay high-level and non-clinical.")
        return normalized

    @field_validator("constraints")
    @classmethod
    def validate_constraints(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, constraint in value.items():
            normalized_key = key.strip()
            normalized_constraint = constraint.strip()
            if not normalized_key or len(normalized_key) > 64:
                raise ValueError("Constraint keys must be non-empty and at most 64 chars.")
            if not normalized_constraint:
                raise ValueError("Constraints must be non-empty high-level indicators.")
            has_clinical_key = normalized_key.lower() in CLINICAL_GOAL_DETAIL_KEYS
            if has_clinical_key or _contains_clinical_goal_detail(normalized_constraint):
                raise ValueError("Goal constraints must stay high-level and non-clinical.")
            if "\n" in constraint or len(normalized_constraint) > 240:
                raise ValueError("Constraints must be short high-level indicators.")
            normalized[normalized_key] = normalized_constraint
        return normalized


class GoalResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    id: UUID
    category: GoalCategory
    priority: int = Field(ge=1, le=5)
    time_horizon: GoalTimeHorizon
    success_metric: str
    constraints: dict[str, str] = Field(default_factory=dict)
    active: bool


class ActiveGoal(ContractModel):
    goal_id: UUID
    priority_order: int = Field(ge=1)
    category: GoalCategory
    priority: int = Field(ge=1, le=5)
    time_horizon: GoalTimeHorizon
    success_metric: str
    constraints: dict[str, str] = Field(default_factory=dict)


class ActiveGoalSet(ContractModel):
    schema_version: Literal["v1"] = "v1"
    user_id: UUID
    goals: list[ActiveGoal] = Field(default_factory=list)
    category_priorities: dict[str, int] = Field(default_factory=dict)
    horizons_by_category: dict[str, list[GoalTimeHorizon]] = Field(default_factory=dict)
    constraints_by_category: dict[str, list[dict[str, str]]] = Field(default_factory=dict)


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


class BriefingTraceInspection(ContractModel):
    schema_version: Literal["v1"] = "v1"
    trace_id: UUID
    data_freshness: DataFreshness | None = None
    feature_values: list[PersonalEvidence] = Field(default_factory=list)
    rules_fired: list[str] = Field(default_factory=list)
    retrieved_memory: list[MemoryObservation] = Field(default_factory=list)
    external_sources: list[ExternalCitation] = Field(default_factory=list)
    model_metadata: dict[str, str] = Field(default_factory=dict)


class CandidateOption(ContractModel):
    label: str = Field(min_length=1)
    recommendation_band: RecommendationBand
    rationale: str = Field(min_length=1)


class GoalTradeoff(ContractModel):
    goal: str = Field(min_length=1)
    tradeoff: str = Field(min_length=1)
    indicator_status: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


def _default_recommendation_summary() -> RecommendationSummary:
    return RecommendationSummary(primary="Review the recommendation band and candidate options.")


class DailyBriefingResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    date: dt.date
    readiness_state: ReadinessState
    confidence: ConfidenceLevel
    data_freshness: DataFreshness
    evidence: list[PersonalEvidence] = Field(min_length=1)
    memory_observations: list[MemoryObservation] = Field(default_factory=list)
    external_citations: list[ExternalCitation] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    recommendation: RecommendationSummary = Field(default_factory=_default_recommendation_summary)
    recommendation_band: RecommendationBand
    candidate_options: list[CandidateOption] = Field(default_factory=list)
    goal_tradeoffs: list[GoalTradeoff] = Field(default_factory=list)
    uncertainty: list[str] = Field(min_length=1)
    data_quality_notes: list[DataQualityNote] = Field(default_factory=list)
    what_would_change_my_mind: list[str] = Field(default_factory=list)
    alternatives: list[RecommendationAlternative] = Field(default_factory=list)
    follow_up: FollowUpPrompt | None = None
    safety_status: SafetyStatus = SafetyStatus.passed
    safety_notes: list[str] = Field(min_length=1)
    trace_id: UUID
    generated_at: dt.datetime
    recommendation_id: UUID | None = None


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
    reason: str | None = Field(default=None, min_length=1, max_length=FEEDBACK_TEXT_MAX_LENGTH)
    outcome_notes: str | None = Field(
        default=None,
        min_length=1,
        max_length=FEEDBACK_TEXT_MAX_LENGTH,
    )

    @field_validator("reason", "outcome_notes")
    @classmethod
    def validate_feedback_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if "\n" in normalized:
            raise ValueError("Feedback text must be a short high-level indicator.")
        return normalized


class FeedbackContradictionAlert(ContractModel):
    contradiction_key: str = Field(min_length=1)
    count: int = Field(ge=2)
    message: str = Field(min_length=1)


class RecommendationFeedbackResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    feedback_id: UUID
    memory_update_status: MemoryUpdateStatus
    eval_queue_status: EvalQueueStatus
    contradiction_alert: FeedbackContradictionAlert | None = None


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
    download_url: str | None = None
    encryption: dict[str, str] = Field(default_factory=dict)


class ConsentRecordRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    consent_version: str = Field(min_length=1)
    health_categories_enabled: list[HealthConsentCategory] = Field(default_factory=list)
    cloud_processing_enabled: bool = False
    external_llm_enabled: bool = False
    raw_note_processing_enabled: bool = False
    privacy_mode: PrivacyMode | None = None


class ConsentRecordResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    id: UUID
    user_id: UUID
    consent_version: str
    health_categories_enabled: list[HealthConsentCategory] = Field(default_factory=list)
    cloud_processing_enabled: bool
    external_llm_enabled: bool
    raw_note_processing_enabled: bool
    timestamp: dt.datetime
    revoked_at: dt.datetime | None = None


class ConsentHistoryResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    active_consent_version: str
    records: list[ConsentRecordResponse] = Field(default_factory=list)


class DisableExternalLLMRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    consent_version: str | None = Field(default=None, min_length=1)


class ConsentRevocationRequest(ContractModel):
    schema_version: Literal["v1"] = "v1"
    consent_version: str | None = Field(default=None, min_length=1)
    revoke_cloud_processing: bool = True
    revoke_external_llm: bool = True
    revoke_raw_note_processing: bool = True
    revoke_health_categories: list[HealthConsentCategory] | None = None


class DataDeleteResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    deleted: dict[str, int] = Field(default_factory=dict)


class ModelDisclosureRecord(ContractModel):
    run_id: UUID
    created_at: dt.datetime
    run_type: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    input_hash: str
    payload_metadata: dict[str, Any] = Field(default_factory=dict)


class ModelDisclosureResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    runs: list[ModelDisclosureRecord] = Field(default_factory=list)


class LLMSettingsResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    provider: str
    cheap_model: str
    strong_model: str
    fallback_model: str


class MemorySummaryItem(ContractModel):
    memory_summary_id: UUID
    period_type: str
    start_date: dt.date
    end_date: dt.date
    summary_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    sensitive_fields_excluded: list[str] = Field(default_factory=list)


class MemorySummaryListResponse(ContractModel):
    schema_version: Literal["v1"] = "v1"
    summaries: list[MemorySummaryItem] = Field(default_factory=list)


class MemoryCorrectionRequest(ContractModel):
    """Request body for `POST /v1/data/memory-summaries/{memory_summary_id}/correct`.

    At least one of `observations` or `hypotheses` must be provided. Items are
    structurally validated by `MemoryService.correct_summary`, which is the
    single source of truth for the corrected-item shape.
    """

    schema_version: Literal["v1"] = "v1"
    observations: list[dict[str, Any]] | None = Field(default=None)
    hypotheses: list[dict[str, Any]] | None = Field(default=None)


__all__ = [
    "ActiveGoal",
    "ActiveGoalSet",
    "AssistantQueryRequest",
    "AssistantQueryResponse",
    "BriefingTraceInspection",
    "CandidateOption",
    "DailyAnalysisRequest",
    "DailyAnalysisResponse",
    "DailyBriefingResponse",
    "DailyCheckInRequest",
    "DailyCheckInResponse",
    "DataExportRequest",
    "DataExportResponse",
    "ConsentHistoryResponse",
    "ConsentRecordRequest",
    "ConsentRecordResponse",
    "ConsentRevocationRequest",
    "DataDeleteResponse",
    "DisableExternalLLMRequest",
    "ModelDisclosureRecord",
    "ModelDisclosureResponse",
    "DataFreshness",
    "DataQualitySummary",
    "GoalRequest",
    "GoalResponse",
    "GoalTradeoff",
    "HealthSamplePayload",
    "HealthSyncRequest",
    "HealthSyncResponse",
    "LLMSettingsResponse",
    "MemoryCorrectionRequest",
    "MemorySummaryItem",
    "MemorySummaryListResponse",
    "RecommendationAlternative",
    "RecommendationFeedbackRequest",
    "RecommendationFeedbackResponse",
]
