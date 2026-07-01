"""Raw sample and normalized metric repositories."""

from sqlmodel import Session

from baseline_api.db.models.ingestion import NormalizedHealthMetric, RawHealthSample
from baseline_api.db.repositories.base import BaseRepository


class RawHealthSampleRepository(BaseRepository[RawHealthSample]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, RawHealthSample)


class NormalizedHealthMetricRepository(BaseRepository[NormalizedHealthMetric]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, NormalizedHealthMetric)
