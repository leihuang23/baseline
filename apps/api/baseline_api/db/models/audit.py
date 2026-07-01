"""Audit event log.

Data classification: Internal after redaction (must not contain raw health data or
free-text notes).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import AuditEventType, RedactionStatus


class AuditEvent(BaseDBModel, table=True):
    """Redacted audit trail of user and system actions."""

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_user_id_timestamp", "user_id", "timestamp"),)

    user_id: UUID | None = Field(
        default=None,
        foreign_key="user.id",
    )
    event_type: AuditEventType = Field(
        sa_column=Column(
            SAEnum(AuditEventType, native_enum=True),
            nullable=False,
        ),
    )
    actor: str = Field(nullable=False)
    timestamp: datetime = Field(nullable=False)
    event_metadata: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    redaction_status: RedactionStatus = Field(
        sa_column=Column(
            SAEnum(RedactionStatus, native_enum=True),
            nullable=False,
        ),
    )
