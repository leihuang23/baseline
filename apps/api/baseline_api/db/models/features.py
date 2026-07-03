"""Deterministic derived daily features.

Data classification: Confidential (derived daily features).
"""

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel


class DerivedDailyFeature(BaseDBModel, table=True):
    """Versioned, deterministic feature bundle for a single calendar day."""

    __tablename__ = "derived_daily_feature"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_derived_daily_feature_user_date"),
        Index("ix_derived_daily_feature_user_id_date", "user_id", "date"),
    )

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    date: dt.date = Field(nullable=False)
    feature_version: str = Field(nullable=False)
    sleep_features: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    hrv_features: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    rhr_features: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    training_load_features: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    recovery_features: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    goal_features: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    data_quality: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    anomaly_flags: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    computed_at: dt.datetime = Field(nullable=False)
    source_sample_ids: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
