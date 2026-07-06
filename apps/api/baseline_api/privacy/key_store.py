"""Durable-but-ephemeral export encryption key storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID


class ExportKeyStore(Protocol):
    """Hold an export decryption key only until the download link expires."""

    def store_key(
        self,
        job_id: UUID,
        key: bytes,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        """Persist the key until expiry."""

    def get_key(self, job_id: UUID) -> bytes | None:
        """Return the key if it has not expired, otherwise None."""

    def delete_key(self, job_id: UUID) -> None:
        """Remove the key immediately."""


class MemoryExportKeyStore:
    """Process-local key store for tests and single-process local deployments."""

    def __init__(self) -> None:
        self._keys: dict[UUID, bytes] = {}
        self._expires_at: dict[UUID, datetime] = {}

    def store_key(
        self,
        job_id: UUID,
        key: bytes,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self._keys[job_id] = key
        if ttl_seconds is not None:
            self._expires_at[job_id] = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

    def get_key(self, job_id: UUID) -> bytes | None:
        expires_at = self._expires_at.get(job_id)
        if expires_at is not None and datetime.now(UTC) >= expires_at:
            self.delete_key(job_id)
            return None
        return self._keys.get(job_id)

    def delete_key(self, job_id: UUID) -> None:
        self._keys.pop(job_id, None)
        self._expires_at.pop(job_id, None)


class RedisExportKeyStore:
    """Redis-backed key store with TTL for production deployments."""

    def __init__(self, redis_url: str, prefix: str = "baseline:export:key:") -> None:
        # Import lazily so environments without Redis can still use the memory store.
        from redis import Redis

        self._client: Redis = Redis.from_url(redis_url)
        self._prefix = prefix

    def _key(self, job_id: UUID) -> str:
        return f"{self._prefix}{job_id}"

    def store_key(
        self,
        job_id: UUID,
        key: bytes,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        if ttl_seconds is None or ttl_seconds <= 0:
            raise ValueError("RedisExportKeyStore requires a positive ttl_seconds.")
        self._client.setex(self._key(job_id), ttl_seconds, key)

    def get_key(self, job_id: UUID) -> bytes | None:
        value = self._client.get(self._key(job_id))
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        return cast(str, value).encode("utf-8")

    def delete_key(self, job_id: UUID) -> None:
        self._client.delete(self._key(job_id))
