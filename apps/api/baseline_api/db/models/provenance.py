"""Foreign-key provenance links between raw, normalized, session, and feature records."""

from uuid import UUID

from sqlmodel import Field, SQLModel


class NormalizedHealthMetricSourceSample(SQLModel, table=True):
    """Raw samples that contributed to a normalized metric."""

    __tablename__ = "normalized_health_metric_source_sample"

    normalized_health_metric_id: UUID = Field(
        foreign_key="normalized_health_metric.id",
        primary_key=True,
    )
    raw_health_sample_id: UUID = Field(
        foreign_key="raw_health_sample.id",
        primary_key=True,
    )


class WorkoutSessionSourceSample(SQLModel, table=True):
    """Raw samples that contributed to a workout session."""

    __tablename__ = "workout_session_source_sample"

    workout_session_id: UUID = Field(
        foreign_key="workout_session.id",
        primary_key=True,
    )
    raw_health_sample_id: UUID = Field(
        foreign_key="raw_health_sample.id",
        primary_key=True,
    )


class SleepSessionSourceSample(SQLModel, table=True):
    """Raw samples that contributed to a sleep session."""

    __tablename__ = "sleep_session_source_sample"

    sleep_session_id: UUID = Field(
        foreign_key="sleep_session.id",
        primary_key=True,
    )
    raw_health_sample_id: UUID = Field(
        foreign_key="raw_health_sample.id",
        primary_key=True,
    )


class DerivedDailyFeatureSourceSample(SQLModel, table=True):
    """Raw samples that contributed directly to a derived daily feature."""

    __tablename__ = "derived_daily_feature_source_sample"

    derived_daily_feature_id: UUID = Field(
        foreign_key="derived_daily_feature.id",
        primary_key=True,
    )
    raw_health_sample_id: UUID = Field(
        foreign_key="raw_health_sample.id",
        primary_key=True,
    )


class DerivedDailyFeatureSourceMetric(SQLModel, table=True):
    """Normalized metrics that contributed to a derived daily feature."""

    __tablename__ = "derived_daily_feature_source_metric"

    derived_daily_feature_id: UUID = Field(
        foreign_key="derived_daily_feature.id",
        primary_key=True,
    )
    normalized_health_metric_id: UUID = Field(
        foreign_key="normalized_health_metric.id",
        primary_key=True,
    )
