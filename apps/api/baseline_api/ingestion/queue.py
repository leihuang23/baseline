"""Normalization job enqueue boundary for ingestion."""

from typing import Any, Protocol
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings


class NormalizationJobQueue(Protocol):
    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str | None:
        """Queue normalization for an accepted raw import batch."""


class ArqNormalizationJobQueue:
    _pools: dict[str, Any] = {}

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        if redis_url not in self._pools:
            self._pools[redis_url] = create_pool(RedisSettings.from_dsn(redis_url))

    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str | None:
        redis: Any = await self._pools[self._redis_url]
        job = await redis.enqueue_job(
            "normalize_health_batch",
            str(import_batch_id),
            str(user_id),
            _job_id=f"normalize-health-batch:{import_batch_id}",
        )
        return None if job is None else str(job.job_id)
