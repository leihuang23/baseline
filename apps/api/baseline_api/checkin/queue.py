"""Daily analysis job enqueue boundary for check-ins."""

from __future__ import annotations

import datetime as dt
from inspect import isawaitable
from typing import Any, Protocol
from uuid import UUID, uuid4

from arq import create_pool
from arq.connections import RedisSettings


class AnalysisJobQueue(Protocol):
    async def enqueue_daily_analysis(
        self,
        *,
        checkin_id: UUID,
        user_id: UUID,
        date: dt.date,
    ) -> UUID | None:
        """Queue the daily feature/analysis pipeline for a submitted check-in."""


class ArqAnalysisJobQueue:
    """Redis-backed queue for daily analysis jobs."""

    def __init__(self, redis_url: str) -> None:
        self._redis_settings = RedisSettings.from_dsn(redis_url)

    async def enqueue_daily_analysis(
        self,
        *,
        checkin_id: UUID,
        user_id: UUID,
        date: dt.date,
    ) -> UUID | None:
        job_id = uuid4()
        redis: Any = await create_pool(self._redis_settings)
        try:
            job = await redis.enqueue_job(
                "daily_analysis",
                str(checkin_id),
                str(user_id),
                date.isoformat(),
                _job_id=str(job_id),
            )
            return None if job is None else job_id
        finally:
            aclose = getattr(redis, "aclose", None)
            if aclose is not None:
                await aclose()
            else:
                close = getattr(redis, "close", None)
                if close is not None:
                    result = close()
                    if isawaitable(result):
                        await result
                wait_closed = getattr(redis, "wait_closed", None)
                if wait_closed is not None:
                    await wait_closed()
