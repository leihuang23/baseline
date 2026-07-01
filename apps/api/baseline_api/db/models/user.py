"""User identity and consent records.

Data classification:
- User: Restricted (identifiable account data).
- ConsentRecord: Restricted (records sensitive consent choices over health data).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import PrivacyMode


class User(BaseDBModel, table=True):
    """A single Baseline user/account."""

    __tablename__ = "user"
    __table_args__ = (Index("ix_user_created_at", "created_at"),)

    timezone: str = Field(default="UTC", nullable=False)
    locale: str = Field(default="en", nullable=False)
    privacy_mode: PrivacyMode = Field(
        sa_column=Column(
            SAEnum(PrivacyMode, native_enum=True),
            nullable=False,
        ),
    )
    active_consent_version: str = Field(nullable=False)


class ConsentRecord(BaseDBModel, table=True):
    """Snapshot of consent choices at a point in time."""

    __tablename__ = "consent_record"
    __table_args__ = (
        Index("ix_consent_record_user_id", "user_id"),
        Index("ix_consent_record_timestamp", "timestamp"),
    )

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    consent_version: str = Field(nullable=False)
    health_categories_enabled: list[str] = Field(
        sa_type=JSONB,
        default_factory=list,
    )
    cloud_processing_enabled: bool = Field(nullable=False, default=False)
    external_llm_enabled: bool = Field(nullable=False, default=False)
    raw_note_processing_enabled: bool = Field(nullable=False, default=False)
    timestamp: datetime = Field(nullable=False)
    revoked_at: datetime | None = Field(default=None)
