"""Reasoning outputs: readiness assessments and user-facing recommendations.

Data classification:
- ReadinessAssessment: Confidential (internal reasoning artifact).
- Recommendation: Restricted (user-facing model output with personal health interpretation).
"""

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import (
    ConfidenceLevel,
    ReadinessState,
    RecommendationBand,
    RecommendationType,
    SafetyStatus,
)


class ReasoningTrace(BaseDBModel, table=True):
    """Machine-readable deterministic reasoning trace for an assessment."""

    __tablename__ = "reasoning_trace"
    __table_args__ = (Index("ix_reasoning_trace_user_id_date", "user_id", "date"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    date: dt.date = Field(nullable=False)
    trace_version: str = Field(nullable=False)
    assessment_version: str = Field(nullable=False)
    input_hash: str = Field(nullable=False)
    rules_fired: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    hard_safety_flags: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    trace_payload: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )


class ReadinessAssessment(BaseDBModel, table=True):
    """Structured readiness assessment produced by the deterministic reasoning engine."""

    __tablename__ = "readiness_assessment"
    __table_args__ = (Index("ix_readiness_assessment_user_id_date", "user_id", "date"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    date: dt.date = Field(nullable=False)
    assessment_version: str = Field(nullable=False)
    readiness_state: ReadinessState = Field(
        sa_column=Column(
            SAEnum(ReadinessState, native_enum=True),
            nullable=False,
        ),
    )
    recommendation_band: RecommendationBand = Field(
        sa_column=Column(
            SAEnum(RecommendationBand, native_enum=True),
            nullable=False,
        ),
    )
    confidence: ConfidenceLevel = Field(
        sa_column=Column(
            SAEnum(ConfidenceLevel, native_enum=True),
            nullable=False,
        ),
    )
    uncertainty: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    evidence_items: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    risk_flags: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    candidate_options: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    follow_up_questions: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    goal_tradeoffs: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    hard_safety_flags: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    reasoning_trace_id: UUID = Field(nullable=False)


class Recommendation(BaseDBModel, table=True):
    """User-facing recommendation generated from an assessment and optional LLM run."""

    __tablename__ = "recommendation"
    __table_args__ = (Index("ix_recommendation_user_id_date", "user_id", "date"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    date: dt.date = Field(nullable=False)
    recommendation_type: RecommendationType = Field(
        sa_column=Column(
            SAEnum(RecommendationType, native_enum=True),
            nullable=False,
        ),
    )
    recommendation_text: str = Field(nullable=False)
    candidate_options: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    evidence_refs: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    safety_status: SafetyStatus = Field(
        sa_column=Column(
            SAEnum(SafetyStatus, native_enum=True),
            nullable=False,
        ),
    )
    safety_result: dict[str, Any] = Field(
        sa_type=JSONB,
        nullable=False,
    )
    model_run_id: UUID | None = Field(
        default=None,
        foreign_key="model_run.id",
    )
    reasoning_trace_id: UUID | None = Field(
        default=None,
        foreign_key="reasoning_trace.id",
    )
    briefing_payload: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    accepted_action: dict[str, Any] | None = Field(
        sa_type=JSONB,
        default=None,
    )
    user_feedback: dict[str, Any] | None = Field(
        sa_type=JSONB,
        default=None,
    )


class DailyAnalysisJob(BaseDBModel, table=True):
    """Persisted status for a requested daily briefing pipeline run."""

    __tablename__ = "daily_analysis_job"
    __table_args__ = (Index("ix_daily_analysis_job_user_id_date", "user_id", "date"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    date: dt.date = Field(nullable=False)
    status: str = Field(nullable=False)
    force_recompute: bool = Field(nullable=False, default=False)
    include_external_knowledge: bool = Field(nullable=False, default=False)
    privacy_mode: str = Field(nullable=False)
    request_trace_id: str = Field(nullable=False)
    reasoning_trace_id: UUID | None = Field(
        default=None,
        foreign_key="reasoning_trace.id",
    )
    recommendation_id: UUID | None = Field(
        default=None,
        foreign_key="recommendation.id",
    )
    stage_trace: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    started_at: dt.datetime | None = Field(default=None)
    completed_at: dt.datetime | None = Field(default=None)
