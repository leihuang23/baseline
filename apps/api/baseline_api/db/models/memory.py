"""Compressed personal memory summaries.

Data classification: Confidential (memory summaries and learned patterns).
"""

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import PeriodType


class MemorySummary(BaseDBModel, table=True):
    """Versioned summary of a daily, weekly, monthly, or quarterly period."""

    __tablename__ = "memory_summary"
    __table_args__ = (
        Index(
            "ix_memory_summary_user_id_period",
            "user_id",
            "start_date",
            "end_date",
        ),
    )

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    period_type: PeriodType = Field(
        sa_column=Column(
            SAEnum(PeriodType, native_enum=True),
            nullable=False,
        ),
    )
    start_date: dt.date = Field(nullable=False)
    end_date: dt.date = Field(nullable=False)
    summary_version: str = Field(nullable=False)
    observations: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    hypotheses: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    confidence: float = Field(nullable=False, default=1.0)
    source_refs: list[dict[str, Any]] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    sensitive_fields_excluded: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
