"""arq worker functions for scheduled memory compaction."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, col, select

from baseline_api.db.models.memory import MemorySummary
from baseline_api.db.models.user import User
from baseline_api.memory.service import MemoryService


def _utc_now() -> dt.datetime:
    """Return the current UTC time; isolated for test patching."""
    return dt.datetime.now(dt.UTC)


def _single_user(session: Session) -> User | None:
    return session.exec(select(User).order_by(col(User.created_at)).limit(1)).first()


def _previous_week_bounds(today: dt.date) -> tuple[dt.date, dt.date]:
    """Return the Monday-Sunday bounds for the week before ``today``."""

    start = today - dt.timedelta(days=today.weekday() + 7)
    end = start + dt.timedelta(days=6)
    return start, end


def _previous_month_bounds(today: dt.date) -> tuple[dt.date, dt.date]:
    """Return the calendar-month bounds for the month before ``today``."""

    first_of_month = today.replace(day=1)
    end = first_of_month - dt.timedelta(days=1)
    start = end.replace(day=1)
    return start, end


def _previous_quarter_bounds(today: dt.date) -> tuple[dt.date, dt.date]:
    """Return the calendar-quarter bounds for the quarter before ``today``."""

    current_quarter = (today.month - 1) // 3
    previous_quarter = current_quarter - 1
    year = today.year
    if previous_quarter < 0:
        year -= 1
        previous_quarter = 3
    start_month = previous_quarter * 3 + 1
    start = dt.date(year, start_month, 1)
    # The current quarter start is the day after the previous quarter's end.
    current_start_month = current_quarter * 3 + 1
    current_year = today.year
    if current_start_month > 12:
        current_start_month -= 12
        current_year += 1
    end = dt.date(current_year, current_start_month, 1) - dt.timedelta(days=1)
    return start, end


def _compact_period(
    session_maker: sessionmaker[Session],
    today: dt.date,
    start_date: dt.date,
    end_date: dt.date,
    period: str,
) -> dict[str, Any]:
    with session_maker() as session:
        user = _single_user(session)
        if user is None:
            return {"status": "error", "error": "user_not_found", "period": period}
        service = MemoryService(session)
        summary: MemorySummary
        if period == "weekly":
            summary = service.generate_weekly_summary(
                user_id=user.id,
                start_date=start_date,
                end_date=end_date,
            )
        elif period == "monthly":
            summary = service.generate_monthly_summary(
                user_id=user.id,
                start_date=start_date,
                end_date=end_date,
            )
        elif period == "quarterly":
            summary = service.generate_quarterly_summary(
                user_id=user.id,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            return {"status": "error", "error": "unknown_period", "period": period}
        return {
            "status": "success",
            "period": period,
            "memory_summary_id": str(summary.id),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "user_id": str(user.id),
        }


async def compact_weekly_memory(ctx: dict[str, Any]) -> dict[str, Any]:
    """Compact the previous week's daily summaries into a weekly memory summary."""

    session_maker: sessionmaker[Session] = ctx["session_maker"]
    today = _utc_now().date()
    start_date, end_date = _previous_week_bounds(today)
    return _compact_period(session_maker, today, start_date, end_date, "weekly")


async def compact_monthly_memory(ctx: dict[str, Any]) -> dict[str, Any]:
    """Compact the previous month's summaries into a monthly memory summary."""

    session_maker: sessionmaker[Session] = ctx["session_maker"]
    today = _utc_now().date()
    start_date, end_date = _previous_month_bounds(today)
    return _compact_period(session_maker, today, start_date, end_date, "monthly")


async def compact_quarterly_memory(ctx: dict[str, Any]) -> dict[str, Any]:
    """Compact the previous quarter's summaries into a quarterly memory summary."""

    session_maker: sessionmaker[Session] = ctx["session_maker"]
    today = _utc_now().date()
    start_date, end_date = _previous_quarter_bounds(today)
    return _compact_period(session_maker, today, start_date, end_date, "quarterly")
