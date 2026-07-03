"""Raw source samples and normalized canonical metrics.

Data classification:
- RawHealthSample: Restricted (raw HealthKit samples).
- NormalizedHealthMetric: Confidential (canonical derived-from-raw metrics).
"""

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import MetricType


class HealthImportBatch(BaseDBModel, table=True):
    """A client sync request tracked for idempotency and resume anchors."""

    __tablename__ = "health_import_batch"
    __table_args__ = (
        UniqueConstraint("user_id", "client_sync_id", name="uq_health_import_batch_user_client"),
        Index("ix_health_import_batch_user_id_imported_at", "user_id", "imported_at"),
    )

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    client_sync_id: str = Field(nullable=False)
    request_hash: str = Field(nullable=False)
    source_platform: str = Field(nullable=False)
    source_device: str = Field(nullable=False)
    timezone: str = Field(nullable=False)
    last_anchor: str | None = Field(default=None)
    next_anchor: str = Field(nullable=False)
    accepted_count: int = Field(nullable=False, default=0)
    duplicate_count: int = Field(nullable=False, default=0)
    rejected_count: int = Field(nullable=False, default=0)
    warnings: list[str] = Field(sa_type=JSONB, default_factory=list)
    data_quality_summary: dict[str, Any] = Field(sa_type=JSONB, default_factory=dict)
    normalization_job_id: str | None = Field(default=None)
    imported_at: dt.datetime = Field(nullable=False)


class BackfillJob(BaseDBModel, table=True):
    """Progress state for chunked historical health imports."""

    __tablename__ = "backfill_job"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_platform",
            "start_date",
            "end_date",
            name="uq_backfill_job_user_source_range",
        ),
        Index("ix_backfill_job_user_id_status", "user_id", "status"),
    )

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    source_platform: str = Field(nullable=False)
    source_device: str = Field(nullable=False)
    timezone: str = Field(nullable=False)
    start_date: dt.date = Field(nullable=False)
    end_date: dt.date = Field(nullable=False)
    chunk_days: int = Field(nullable=False)
    next_start_date: dt.date = Field(nullable=False)
    processed_days: int = Field(nullable=False, default=0)
    status: str = Field(nullable=False, default="running")
    accepted_count: int = Field(nullable=False, default=0)
    duplicate_count: int = Field(nullable=False, default=0)
    rejected_count: int = Field(nullable=False, default=0)
    last_error: str | None = Field(default=None)
    completed_at: dt.datetime | None = Field(default=None)


class DailyDataQuality(BaseDBModel, table=True):
    """Per-day completeness and freshness read model for downstream consumers."""

    __tablename__ = "daily_data_quality"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_daily_data_quality_user_date"),
        Index("ix_daily_data_quality_user_id_date", "user_id", "date"),
    )

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    date: dt.date = Field(nullable=False)
    expected_types: list[str] = Field(sa_type=JSONB, default_factory=list)
    present_types: list[str] = Field(sa_type=JSONB, default_factory=list)
    missing_types: list[str] = Field(sa_type=JSONB, default_factory=list)
    completeness_ratio: float = Field(nullable=False)
    completeness_warnings: list[dict[str, Any]] = Field(sa_type=JSONB, default_factory=list)
    freshness_by_type: dict[str, Any] = Field(sa_type=JSONB, default_factory=dict)
    stale_types: list[str] = Field(sa_type=JSONB, default_factory=list)
    computed_at: dt.datetime = Field(nullable=False)


class RawHealthSample(BaseDBModel, table=True):
    """A single raw sample imported from a source platform (e.g. Apple Health)."""

    __tablename__ = "raw_health_sample"
    __table_args__ = (
        Index("ix_raw_health_sample_user_id_start_time", "user_id", "start_time"),
        UniqueConstraint(
            "user_id",
            "source_platform",
            "source_sample_id",
            "content_hash",
            name="uq_raw_health_sample_source_hash",
        ),
    )

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    source_platform: str = Field(nullable=False)
    source_device: str | None = Field(default=None)
    source_sample_id: str = Field(nullable=False)
    content_hash: str = Field(default="", nullable=False)
    sample_type: MetricType = Field(
        sa_column=Column(
            SAEnum(MetricType, native_enum=True),
            nullable=False,
        ),
    )
    start_time: dt.datetime = Field(nullable=False)
    end_time: dt.datetime | None = Field(default=None)
    raw_value: float = Field(nullable=False)
    raw_unit: str = Field(nullable=False)
    source_metadata: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    imported_at: dt.datetime = Field(nullable=False)
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
    start_time: dt.datetime = Field(nullable=False)
    end_time: dt.datetime | None = Field(default=None)
    value: float = Field(nullable=False)
    unit: str = Field(nullable=False)
    confidence: float = Field(nullable=False, default=1.0)
    source_sample_ids: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    normalization_version: str = Field(nullable=False)
