"""Readiness assessment and recommendation repositories."""

import datetime as dt
from uuid import UUID

from sqlmodel import Session, select

from baseline_api.db.models.assessment import ReadinessAssessment, ReasoningTrace, Recommendation
from baseline_api.db.repositories.base import BaseRepository


class ReadinessAssessmentRepository(BaseRepository[ReadinessAssessment]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ReadinessAssessment)

    def get_by_user_date_trace(
        self,
        *,
        user_id: UUID,
        date: dt.date,
        reasoning_trace_id: UUID,
    ) -> ReadinessAssessment | None:
        statement = select(ReadinessAssessment).where(
            ReadinessAssessment.user_id == user_id,
            ReadinessAssessment.date == date,
            ReadinessAssessment.reasoning_trace_id == reasoning_trace_id,
        )
        return self.session.exec(statement).first()


class ReasoningTraceRepository(BaseRepository[ReasoningTrace]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ReasoningTrace)


class RecommendationRepository(BaseRepository[Recommendation]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Recommendation)
