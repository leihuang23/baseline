"""Memory summary repository."""

from sqlmodel import Session

from baseline_api.db.models.memory import MemorySummary
from baseline_api.db.repositories.base import BaseRepository


class MemorySummaryRepository(BaseRepository[MemorySummary]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, MemorySummary)
