"""Readiness assessment and recommendation repositories."""

from sqlmodel import Session

from baseline_api.db.models.assessment import ReadinessAssessment, Recommendation
from baseline_api.db.repositories.base import BaseRepository


class ReadinessAssessmentRepository(BaseRepository[ReadinessAssessment]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ReadinessAssessment)


class RecommendationRepository(BaseRepository[Recommendation]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Recommendation)
