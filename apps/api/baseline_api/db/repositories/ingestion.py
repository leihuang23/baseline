"""Raw sample and normalized metric repositories."""

from uuid import UUID

from sqlmodel import Session, select

from baseline_api.db.models.ingestion import (
    HealthImportBatch,
    NormalizedHealthMetric,
    RawHealthSample,
)
from baseline_api.db.repositories.base import BaseRepository


class HealthImportBatchRepository(BaseRepository[HealthImportBatch]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, HealthImportBatch)

    def get_by_client_sync_id(self, user_id: UUID, client_sync_id: str) -> HealthImportBatch | None:
        statement = select(HealthImportBatch).where(
            HealthImportBatch.user_id == user_id,
            HealthImportBatch.client_sync_id == client_sync_id,
        )
        return self.session.exec(statement).first()


class RawHealthSampleRepository(BaseRepository[RawHealthSample]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, RawHealthSample)

    def get_by_source_hash(
        self,
        *,
        user_id: UUID,
        source_platform: str,
        source_sample_id: str,
        content_hash: str,
    ) -> RawHealthSample | None:
        statement = select(RawHealthSample).where(
            RawHealthSample.user_id == user_id,
            RawHealthSample.source_platform == source_platform,
            RawHealthSample.source_sample_id == source_sample_id,
            RawHealthSample.content_hash == content_hash,
        )
        return self.session.exec(statement).first()


class NormalizedHealthMetricRepository(BaseRepository[NormalizedHealthMetric]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, NormalizedHealthMetric)
