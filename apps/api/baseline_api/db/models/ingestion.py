"""Raw source samples and normalized canonical metrics.

Data classification:
- RawHealthSample: Restricted (raw HealthKit samples).
- NormalizedHealthMetric: Confidential (canonical derived-from-raw metrics).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import MetricType


class RawHealthSample(BaseDBModel, table=True):
    """A single raw sample imported from a source platform (e.g. Apple Health)."""

    __tablename__ = "raw_health_sample"
    __table_args__ = (Index("ix_raw_health_sample_user_id_start_time", "user_id", "start_time"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    source_platform: str = Field(nullable=False)
    source_device: str | None = Field(default=None)
    source_sample_id: str = Field(nullable=False)
    sample_type: MetricType = Field(
        sa_column=Column(
            SAEnum(MetricType, native_enum=True),
            nullable=False,
        ),
    )
    start_time: datetime = Field(nullable=False)
    end_time: datetime | None = Field(default=None)
    raw_value: float = Field(nullable=False)
    raw_unit: str = Field(nullable=False)
    source_metadata: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    imported_at: datetime = Field(nullable=False)
    import_batch_id: UUID = Field(nullable=False)


class NormalizedHealthMetric(BaseDBModel, table=True):
    """A canonical, unit-normalized metric produced from one or more raw samples."""

    __tablename__ = "normalized_health_metric"
    __table_args__ = (
        Index(
            "ix_normalized_health_metric_user_id_start_time",
            "user_id",
            "start_time",
        ),
    )

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    metric_type: MetricType = Field(
        sa_column=Column(
            SAEnum(MetricType, native_enum=True),
            nullable=False,
        ),
    )
    start_time: datetime = Field(nullable=False)
    end_time: datetime | None = Field(default=None)
    value: float = Field(nullable=False)
    unit: str = Field(nullable=False)
    confidence: float = Field(nullable=False, default=1.0)
    source_sample_ids: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    normalization_version: str = Field(nullable=False)
