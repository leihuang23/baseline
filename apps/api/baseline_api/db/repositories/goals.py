"""Goal repository."""

from sqlmodel import Session

from baseline_api.db.models.goals import Goal
from baseline_api.db.repositories.base import BaseRepository


class GoalRepository(BaseRepository[Goal]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Goal)
