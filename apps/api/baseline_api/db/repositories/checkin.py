"""Daily check-in repository."""

from sqlmodel import Session

from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.repositories.base import BaseRepository


class DailyCheckInRepository(BaseRepository[DailyCheckIn]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, DailyCheckIn)
