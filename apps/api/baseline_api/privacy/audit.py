"""Redacted audit helpers for privacy controls."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.models.enums import AuditEventType, RedactionStatus


def emit_privacy_audit(
    session: Session,
    *,
    event_type: AuditEventType,
    user_id: UUID | None,
    actor: str = "user",
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        user_id=user_id,
        event_type=event_type,
        actor=actor,
        timestamp=datetime.now(UTC),
        event_metadata=metadata or {},
        redaction_status=RedactionStatus.redacted,
    )
    session.add(event)
    session.flush()
    return event
