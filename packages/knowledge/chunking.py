"""Simple deterministic chunking for curated reference text."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    max_chars: int = 900
    overlap_chars: int = 120

    def __post_init__(self) -> None:
        if self.max_chars < 200:
            raise ValueError("max_chars must be at least 200")
        if self.overlap_chars < 0:
            raise ValueError("overlap_chars cannot be negative")
        if self.overlap_chars >= self.max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")


def chunk_text(text: str, config: ChunkingConfig | None = None) -> list[str]:
    """Split text into stable, overlapping chunks without semantic dependencies."""

    active_config = config or ChunkingConfig()
    normalized = " ".join(text.split())
    if not normalized:
        return []
    if len(normalized) <= active_config.max_chars:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + active_config.max_chars, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start, end)
            if boundary > start + active_config.overlap_chars:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - active_config.overlap_chars, 0)
        while start < len(normalized) and normalized[start] == " ":
            start += 1

    return chunks
