"""Knowledge source repository."""

from sqlmodel import Session

from baseline_api.db.models.knowledge import KnowledgeSource
from baseline_api.db.repositories.base import BaseRepository


class KnowledgeSourceRepository(BaseRepository[KnowledgeSource]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, KnowledgeSource)
