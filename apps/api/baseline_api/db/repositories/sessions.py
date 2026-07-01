"""Workout and sleep session repositories."""

from sqlmodel import Session

from baseline_api.db.models.sessions import SleepSession, WorkoutSession
from baseline_api.db.repositories.base import BaseRepository


class WorkoutSessionRepository(BaseRepository[WorkoutSession]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, WorkoutSession)


class SleepSessionRepository(BaseRepository[SleepSession]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, SleepSession)
