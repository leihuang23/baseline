"""User goals and constraints.

Data classification: Confidential (personal priorities and constraints).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import GoalCategory, TimeHorizon


class Goal(BaseDBModel, table=True):
    """An active or paused user goal used by the reasoning engine."""

    __tablename__ = "goal"
    __table_args__ = (Index("ix_goal_user_id_active", "user_id", "active"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    category: GoalCategory = Field(
        sa_column=Column(
            SAEnum(GoalCategory, native_enum=True),
            nullable=False,
        ),
    )
    priority: int = Field(nullable=False, ge=1)
    time_horizon: TimeHorizon = Field(
        sa_column=Column(
            SAEnum(TimeHorizon, native_enum=True),
            nullable=False,
        ),
    )
    success_metric: str = Field(nullable=False)
    constraints: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    active: bool = Field(nullable=False, default=True)
    paused_at: datetime | None = Field(default=None)
