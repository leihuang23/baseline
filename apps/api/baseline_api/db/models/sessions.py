"""Session-level activity records.

Data classification:
- WorkoutSession: Confidential (derived session summary).
- SleepSession: Confidential (derived session summary).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import Modality


class WorkoutSession(BaseDBModel, table=True):
    """A single exercise/workout session."""

    __tablename__ = "workout_session"
    __table_args__ = (Index("ix_workout_session_user_id_start_time", "user_id", "start_time"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    start_time: datetime = Field(nullable=False)
    end_time: datetime | None = Field(default=None)
    modality: Modality = Field(
        sa_column=Column(
            SAEnum(Modality, native_enum=True),
            nullable=False,
        ),
    )
    distance: float | None = Field(default=None)
    duration: float = Field(nullable=False)  # seconds
    active_energy: float | None = Field(default=None)
    average_hr: float | None = Field(default=None)
    max_hr: float | None = Field(default=None)
    intensity_zone_distribution: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    perceived_exertion: int | None = Field(default=None, ge=1, le=10)
    muscle_group_tags: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    source_sample_ids: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )


class SleepSession(BaseDBModel, table=True):
    """A single sleep session."""

    __tablename__ = "sleep_session"
    __table_args__ = (Index("ix_sleep_session_user_id_start_time", "user_id", "start_time"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    start_time: datetime = Field(nullable=False)
    end_time: datetime | None = Field(default=None)
    duration: float = Field(nullable=False)  # seconds
    sleep_stage_breakdown: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    interruptions: int | None = Field(default=None)
    quality_proxy: float | None = Field(default=None, ge=0, le=1)
    source_sample_ids: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
