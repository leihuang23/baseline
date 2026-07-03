"""Normalization job enqueue boundary for ingestion."""

from inspect import isawaitable
from typing import Any, Protocol
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings


class NormalizationJobQueue(Protocol):
    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str | None:
        """Queue normalization for an accepted raw import batch."""


class ArqNormalizationJobQueue:
    def __init__(self, redis_url: str) -> None:
        self._redis_settings = RedisSettings.from_dsn(redis_url)

    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str | None:
        redis: Any = await create_pool(self._redis_settings)
        try:
            job = await redis.enqueue_job(
                "normalize_health_batch",
                str(import_batch_id),
                str(user_id),
                _job_id=f"normalize-health-batch:{import_batch_id}",
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
