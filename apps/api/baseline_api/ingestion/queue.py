"""Normalization job enqueue boundary for ingestion."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings


class NormalizationJobQueue(Protocol):
    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str | None:
        """Queue normalization for an accepted raw import batch."""


class ArqNormalizationJobQueue:
    """Redis-backed queue for durable normalization execution."""

    _pools: dict[str, Any] = {}
    _locks: dict[str, asyncio.Lock] = {}

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def _get_pool(self) -> Any:
        """Return a cached ArqRedis pool, creating it once on first use."""

        pool = self._pools.get(self._redis_url)
        if pool is not None:
            return pool
        lock = self._locks.setdefault(self._redis_url, asyncio.Lock())
        async with lock:
            pool = self._pools.get(self._redis_url)
            if pool is not None:
                return pool
            pool = await create_pool(RedisSettings.from_dsn(self._redis_url))
            self._pools[self._redis_url] = pool
            return pool

    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str | None:
        redis = await self._get_pool()
        job = await redis.enqueue_job(
            "normalize_health_batch",
            str(import_batch_id),
            str(user_id),
            _job_id=f"normalize-health-batch:{import_batch_id}",
        )
        return None if job is None else str(job.job_id)
