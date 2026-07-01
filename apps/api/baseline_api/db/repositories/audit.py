"""Audit event repository."""

from sqlmodel import Session

from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.repositories.base import BaseRepository


class AuditEventRepository(BaseRepository[AuditEvent]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, AuditEvent)
