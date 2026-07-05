"""Durable queue boundary for daily briefing jobs."""

from __future__ import annotations

from inspect import isawaitable
from typing import Any, Protocol
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings


class DailyBriefingJobQueue(Protocol):
    async def enqueue_daily_briefing(self, *, job_id: UUID) -> str | None:
        """Queue the full daily briefing pipeline for a persisted job."""


class ArqDailyBriefingJobQueue:
    """Redis-backed queue for durable daily briefing execution."""

    def __init__(self, redis_url: str) -> None:
        self._redis_settings = RedisSettings.from_dsn(redis_url)

    async def enqueue_daily_briefing(self, *, job_id: UUID) -> str | None:
        redis: Any = await create_pool(self._redis_settings)
        try:
            job = await redis.enqueue_job(
                "daily_briefing",
                str(job_id),
                _job_id=f"daily-briefing:{job_id}",
            )
            return None if job is None else str(job.job_id)
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
