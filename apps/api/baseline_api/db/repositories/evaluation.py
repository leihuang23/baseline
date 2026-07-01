"""Evaluation case repository."""

from sqlmodel import Session

from baseline_api.db.models.evaluation import EvaluationCase
from baseline_api.db.repositories.base import BaseRepository


class EvaluationCaseRepository(BaseRepository[EvaluationCase]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, EvaluationCase)
