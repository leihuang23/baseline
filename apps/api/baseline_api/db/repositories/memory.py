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
        return self.between(
            user_id=user_id,
            period_type=PeriodType.daily,
            start_date=start_date,
            end_date=end_date,
        )

    def weekly_between(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
    ) -> list[MemorySummary]:
        return self.between(
            user_id=user_id,
            period_type=PeriodType.weekly,
            start_date=start_date,
            end_date=end_date,
        )

    def monthly_between(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
    ) -> list[MemorySummary]:
        return self.between(
            user_id=user_id,
            period_type=PeriodType.monthly,
            start_date=start_date,
            end_date=end_date,
        )

    def between(
        self,
        *,
        user_id: UUID,
        period_type: PeriodType,
        start_date: dt.date,
        end_date: dt.date,
    ) -> list[MemorySummary]:
        statement = (
            select(MemorySummary)
            .where(
                MemorySummary.user_id == user_id,
                MemorySummary.period_type == period_type,
                MemorySummary.start_date >= start_date,
                MemorySummary.end_date <= end_date,
            )
            .order_by(
                col(MemorySummary.start_date),
                col(MemorySummary.end_date),
                col(MemorySummary.created_at).desc(),
                col(MemorySummary.id).desc(),
            )
        )
        rows = list(self.session.exec(statement).all())
        seen_periods: set[tuple[dt.date, dt.date]] = set()
        latest_rows: list[MemorySummary] = []
        for row in rows:
            period_key = (row.start_date, row.end_date)
            if period_key in seen_periods:
                continue
            seen_periods.add(period_key)
            latest_rows.append(row)
        return latest_rows

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
