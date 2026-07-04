"""Knowledge source repository."""

from packages.knowledge.curation import CurationError
from sqlmodel import Session

from baseline_api.db.models.knowledge import KnowledgeChunk, KnowledgeSource
from baseline_api.db.repositories.base import BaseRepository


class KnowledgeSourceRepository(BaseRepository[KnowledgeSource]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, KnowledgeSource)

    def create(self, instance: KnowledgeSource) -> KnowledgeSource:
        raise CurationError(
            "Knowledge sources must be written through the curated knowledge ingestion pipeline"
        )


class KnowledgeChunkRepository(BaseRepository[KnowledgeChunk]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, KnowledgeChunk)

    def create(self, instance: KnowledgeChunk) -> KnowledgeChunk:
        raise CurationError(
            "Knowledge chunks must be written through the curated knowledge ingestion pipeline"
        )
