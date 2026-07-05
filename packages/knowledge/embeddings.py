"""Deterministic local embeddings for ingestion tests and offline seeding."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from baseline_api.db.models.knowledge import KNOWLEDGE_EMBEDDING_DIMENSION

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(Protocol):
    """Embeds one text chunk into a numeric vector."""

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for text."""


class HashEmbeddingProvider:
    """Small deterministic embedding provider with no network or model dependency."""

    def __init__(self, dimension: int = KNOWLEDGE_EMBEDDING_DIMENSION) -> None:
        if dimension < 4:
            raise ValueError("dimension must be at least 4")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in TOKEN_PATTERN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            return vector
        return [round(value / magnitude, 6) for value in vector]


class HTTPEmbeddingProvider:
    """Production embedding provider for OpenAI-compatible embedding endpoints."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        dimension: int = KNOWLEDGE_EMBEDDING_DIMENSION,
        timeout_seconds: float = 5.0,
        max_retries: int = 1,
        retry_sleep_seconds: float = 0.1,
    ) -> None:
        parsed = urllib.parse.urlparse(api_url)
        if parsed.scheme != "https":
            raise ValueError("HTTPEmbeddingProvider requires an https:// URL")
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds

    def embed(self, text: str) -> list[float]:
        payload = json.dumps({"model": self.model, "input": text}).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirectHandler)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with opener.open(request, timeout=self.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                embedding = _extract_embedding(body)
                if len(embedding) != self.dimension:
                    raise ValueError(
                        f"embedding provider returned {len(embedding)} dimensions; "
                        f"expected {self.dimension}"
                    )
                return embedding
            except (json.JSONDecodeError, TypeError, ValueError):
                raise
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    _sleep_before_retry(exc, self.retry_sleep_seconds)
                    continue
                raise
            except (TimeoutError, OSError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    _sleep_before_retry(None, self.retry_sleep_seconds)
                    continue
                break
        raise last_error or ValueError("embedding request failed")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _sleep_before_retry(
    error: urllib.error.HTTPError | None,
    default_seconds: float,
) -> None:
    retry_after = error.headers.get("Retry-After") if error is not None else None
    try:
        delay = float(retry_after) if retry_after is not None else default_seconds
    except ValueError:
        delay = default_seconds
    if delay > 0:
        time.sleep(min(delay, 1.0))


def _extract_embedding(body: object) -> list[float]:
    if isinstance(body, dict):
        direct = body.get("embedding")
        if isinstance(direct, list):
            return [float(value) for value in direct]
        data = body.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            nested = data[0].get("embedding")
            if isinstance(nested, list):
                return [float(value) for value in nested]
    raise ValueError("embedding response did not contain an embedding vector")
