"""Derived daily feature repository."""

from sqlmodel import Session

from baseline_api.db.models.features import DerivedDailyFeature
from baseline_api.db.repositories.base import BaseRepository


class DerivedDailyFeatureRepository(BaseRepository[DerivedDailyFeature]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, DerivedDailyFeature)
