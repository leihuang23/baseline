"""Deterministic local embeddings for ingestion tests and offline seeding."""

from __future__ import annotations

import hashlib
import math
import re
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
