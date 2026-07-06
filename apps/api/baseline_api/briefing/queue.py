"""Durable queue boundary for daily briefing jobs."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings


class DailyBriefingJobQueue(Protocol):
    async def enqueue_daily_briefing(self, *, job_id: UUID) -> str | None:
        """Queue the full daily briefing pipeline for a persisted job."""


class ArqDailyBriefingJobQueue:
    """Redis-backed queue for durable daily briefing execution."""

    _pools: dict[str, Any] = {}

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        if redis_url not in self._pools:
            self._pools[redis_url] = create_pool(RedisSettings.from_dsn(redis_url))

    async def enqueue_daily_briefing(self, *, job_id: UUID) -> str | None:
        redis: Any = await self._pools[self._redis_url]
        job = await redis.enqueue_job(
            "daily_briefing",
            str(job_id),
            _job_id=f"daily-briefing:{job_id}",
        )
        return None if job is None else str(job.job_id)
