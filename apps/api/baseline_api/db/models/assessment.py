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
    goal_tradeoffs: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
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
    model_run_id: UUID | None = Field(
        default=None,
        foreign_key="model_run.id",
    )
    accepted_action: dict[str, Any] | None = Field(
        sa_type=JSONB,
        default=None,
    )
    user_feedback: dict[str, Any] | None = Field(
        sa_type=JSONB,
        default=None,
    )
