"""Manual daily check-ins.

Data classification: Restricted (manual check-ins, structured notes, and references to
free-text notes).
"""

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import RedactionStatus, SensitiveNotePolicy


class DailyCheckIn(BaseDBModel, table=True):
    """User-submitted morning/lifestyle check-in for a single calendar day."""

    __tablename__ = "daily_check_in"
    __table_args__ = (Index("ix_daily_check_in_user_id_date", "user_id", "date"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    date: dt.date = Field(nullable=False)
    energy_score: int | None = Field(default=None, ge=1, le=10)
    mood_score: int | None = Field(default=None, ge=1, le=10)
    soreness_score: int | None = Field(default=None, ge=1, le=10)
    stress_score: int | None = Field(default=None, ge=1, le=10)
    perceived_recovery_score: int | None = Field(default=None, ge=1, le=10)
    food_quality_score: int | None = Field(default=None, ge=1, le=10)
    alcohol_flag: bool = Field(nullable=False, default=False)
    caffeine_notes: str | None = Field(default=None)
    illness_flag: bool = Field(nullable=False, default=False)
    injury_flag: bool = Field(nullable=False, default=False)
    travel_flag: bool = Field(nullable=False, default=False)
    sensitive_note_policy: SensitiveNotePolicy = Field(
        sa_column=Column(
            SAEnum(SensitiveNotePolicy, native_enum=True),
            nullable=False,
        ),
    )
    redaction_status: RedactionStatus = Field(
        default=RedactionStatus.none,
        sa_column=Column(
            SAEnum(RedactionStatus, native_enum=True),
            nullable=False,
        ),
    )
    structured_notes: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    free_text_note_reference: str | None = Field(default=None)
    free_text_note_summary: str | None = Field(default=None)
    analysis_job_id: UUID | None = Field(default=None)
