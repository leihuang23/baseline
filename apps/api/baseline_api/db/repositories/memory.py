"""Memory summary repository."""

import datetime as dt
from uuid import UUID

from sqlmodel import Session, col, select

from baseline_api.db.models.enums import PeriodType
from baseline_api.db.models.memory import MemorySummary
from baseline_api.db.repositories.base import BaseRepository


class MemorySummaryRepository(BaseRepository[MemorySummary]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, MemorySummary)

    def latest_for_period(
        self,
        *,
        user_id: UUID,
        period_type: PeriodType,
        start_date: dt.date,
        end_date: dt.date,
    ) -> MemorySummary | None:
        statement = (
            select(MemorySummary)
            .where(
                MemorySummary.user_id == user_id,
                MemorySummary.period_type == period_type,
                MemorySummary.start_date == start_date,
                MemorySummary.end_date == end_date,
            )
            .order_by(col(MemorySummary.created_at).desc())
        )
        return self.session.exec(statement).first()

    def daily_between(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
    ) -> list[MemorySummary]:
        statement = (
            select(MemorySummary)
            .where(
                MemorySummary.user_id == user_id,
                MemorySummary.period_type == PeriodType.daily,
                MemorySummary.start_date >= start_date,
                MemorySummary.end_date <= end_date,
            )
            .order_by(col(MemorySummary.start_date))
        )
        return list(self.session.exec(statement).all())

    def recent_before(
        self,
        *,
        user_id: UUID,
        before_date: dt.date,
        limit: int = 5,
    ) -> list[MemorySummary]:
        statement = (
            select(MemorySummary)
            .where(
                MemorySummary.user_id == user_id,
                MemorySummary.end_date < before_date,
            )
            .order_by(col(MemorySummary.end_date).desc(), col(MemorySummary.created_at).desc())
            .limit(limit)
        )
        return list(self.session.exec(statement).all())
