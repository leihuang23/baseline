"""Domain records for curated external knowledge ingestion."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class KnowledgeSourceDocument:
    """A candidate external source and its license/curation metadata."""

    title: str
    author_or_org: str
    source_type: KnowledgeSourceType
    url_or_identifier: str
    license_status: str
    published_at: dt.date
    version: str
    trust_level: TrustLevel | None
    content: str
    citation_urls: tuple[str, ...] = ()
    source_metadata: dict[str, JsonValue] = field(default_factory=dict)
    ingested_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeChunkPayload:
    """Chunk text plus deterministic embedding prepared for storage."""

    chunk_index: int
    text: str
    content_hash: str
    embedding: list[float]


@dataclass(slots=True)
class StoredKnowledgeSource:
    """Stored source lifecycle state returned by a vector store."""

    id: UUID
    title: str
    author_or_org: str
    source_type: KnowledgeSourceType
    url_or_identifier: str
    license_status: str
    published_at: dt.date
    ingested_at: dt.datetime
    version: str
    trust_level: TrustLevel
    superseded_at: dt.datetime | None = None
    removed_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredKnowledgeChunk:
    """Stored chunk with source metadata copied into the vector record."""

    id: UUID
    source_id: UUID
    source_version: str
    chunk_index: int
    text: str
    content_hash: str
    embedding: list[float]
    source_metadata: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Result of one source ingest operation."""

    source: StoredKnowledgeSource
    chunks: list[StoredKnowledgeChunk]
    superseded_source_ids: list[UUID]


def source_key(document_or_source: KnowledgeSourceDocument | StoredKnowledgeSource | str) -> str:
    """Return the stable source identity used for supersede/removal."""

    if isinstance(document_or_source, str):
        raw_key = document_or_source
    else:
        raw_key = document_or_source.url_or_identifier
    return " ".join(raw_key.strip().lower().split())


def stored_source_identifier(document: KnowledgeSourceDocument) -> str:
    """Return the persisted source identifier without changing case or inner spacing."""

    return document.url_or_identifier.strip()


def normalized_citation_urls(citation_urls: tuple[str, ...]) -> tuple[str, ...]:
    """Return non-empty citation URLs/identifiers with surrounding whitespace removed."""

    return tuple(citation_url.strip() for citation_url in citation_urls if citation_url.strip())


def source_metadata(
    source: StoredKnowledgeSource,
    external_metadata: dict[str, JsonValue] | None = None,
    citation_urls: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    """Copy full source metadata into each stored chunk."""

    metadata: dict[str, JsonValue] = {
        "source_id": str(source.id),
        "title": source.title,
        "author_or_org": source.author_or_org,
        "source_type": source.source_type.value,
        "url_or_identifier": source.url_or_identifier,
        "license_status": source.license_status,
        "published_at": source.published_at.isoformat(),
        "ingested_at": source.ingested_at.isoformat(),
        "version": source.version,
        "trust_level": source.trust_level.value,
    }
    metadata.update(external_metadata or {})
    normalized_citations = normalized_citation_urls(citation_urls)
    if normalized_citations:
        metadata["citation_urls"] = list(normalized_citations)
    return metadata
