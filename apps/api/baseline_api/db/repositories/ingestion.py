"""Raw sample, backfill, and data-quality repositories."""

from datetime import date
from uuid import UUID

from sqlmodel import Session, select

from baseline_api.db.models.ingestion import (
    BackfillJob,
    DailyDataQuality,
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


class BackfillJobRepository(BaseRepository[BackfillJob]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, BackfillJob)

    def get_by_range(
        self,
        *,
        user_id: UUID,
        source_platform: str,
        start_date: date,
        end_date: date,
    ) -> BackfillJob | None:
        statement = select(BackfillJob).where(
            BackfillJob.user_id == user_id,
            BackfillJob.source_platform == source_platform,
            BackfillJob.start_date == start_date,
            BackfillJob.end_date == end_date,
        )
        return self.session.exec(statement).first()


class DailyDataQualityRepository(BaseRepository[DailyDataQuality]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, DailyDataQuality)

    def get_by_user_day(self, *, user_id: UUID, day: date) -> DailyDataQuality | None:
        statement = select(DailyDataQuality).where(
            DailyDataQuality.user_id == user_id,
            DailyDataQuality.date == day,
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
