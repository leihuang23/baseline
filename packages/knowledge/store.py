"""Vector-store adapters for the curated knowledge corpus."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlmodel import Session, col, select

from baseline_api.db.models.enums import TrustLevel
from baseline_api.db.models.knowledge import KnowledgeChunk, KnowledgeSource, normalize_embedding
from packages.knowledge.curation import (
    ACCEPTED_TRUST_LEVELS,
    validate_chunk_payload,
    validate_document,
)
from packages.knowledge.models import (
    IngestionResult,
    KnowledgeChunkPayload,
    KnowledgeSourceDocument,
    StoredKnowledgeChunk,
    StoredKnowledgeSource,
    source_key,
    source_metadata,
    stored_source_identifier,
)

VERSION_TOKEN_PATTERN = re.compile(r"\d+|[a-z]+")


class VersionedKnowledgeSource(Protocol):
    version: str


class KnowledgeVersionError(ValueError):
    """Raised when an ingest would replace active knowledge with a non-newer version."""


class KnowledgeVectorStore(Protocol):
    """Storage boundary for embedded external knowledge chunks."""

    def upsert_source(
        self,
        document: KnowledgeSourceDocument,
        chunks: list[KnowledgeChunkPayload],
        ingested_at: dt.datetime,
    ) -> IngestionResult:
        """Store a source version and its chunks, superseding older active versions."""

    def remove_source(self, source_identifier: str, removed_at: dt.datetime) -> int:
        """Remove active source versions and purge their chunks."""


class InMemoryKnowledgeVectorStore:
    """In-memory vector store used by deterministic ingestion tests."""

    def __init__(self) -> None:
        self.sources: dict[UUID, StoredKnowledgeSource] = {}
        self.chunks: dict[UUID, StoredKnowledgeChunk] = {}

    def upsert_source(
        self,
        document: KnowledgeSourceDocument,
        chunks: list[KnowledgeChunkPayload],
        ingested_at: dt.datetime,
    ) -> IngestionResult:
        validate_document(document)
        _validate_chunks(chunks)
        key = source_key(document)
        active_sources = [
            source
            for source in self.sources.values()
            if source_key(source) == key
            and source.superseded_at is None
            and source.removed_at is None
        ]
        existing_source = _existing_source_for_version(document.version, active_sources)
        if existing_source is not None:
            return IngestionResult(
                source=existing_source,
                chunks=self.chunks_for_source(existing_source.id),
                superseded_source_ids=[],
            )
        _reject_non_newer_version(document.version, active_sources)

        superseded_ids: list[UUID] = []
        for source in active_sources:
            source.superseded_at = ingested_at
            superseded_ids.append(source.id)

        stored_source = StoredKnowledgeSource(
            id=uuid4(),
            title=document.title,
            author_or_org=document.author_or_org,
            source_type=document.source_type,
            url_or_identifier=stored_source_identifier(document),
            license_status=document.license_status,
            published_at=document.published_at,
            ingested_at=ingested_at,
            version=document.version,
            trust_level=_required_trust_level(document),
        )
        self.sources[stored_source.id] = stored_source
        stored_chunks = [
            self._store_chunk(stored_source, chunk_payload, document) for chunk_payload in chunks
        ]
        return IngestionResult(
            source=stored_source,
            chunks=stored_chunks,
            superseded_source_ids=superseded_ids,
        )

    def remove_source(self, source_identifier: str, removed_at: dt.datetime) -> int:
        key = source_key(source_identifier)
        removed_count = 0
        for source in self.sources.values():
            if source_key(source) == key and source.removed_at is None:
                source.removed_at = removed_at
                self._delete_chunks_for_source(source.id)
                removed_count += 1
        return removed_count

    def chunks_for_source(self, source_id: UUID) -> list[StoredKnowledgeChunk]:
        return [chunk for chunk in self.chunks.values() if chunk.source_id == source_id]

    def _store_chunk(
        self,
        source: StoredKnowledgeSource,
        chunk_payload: KnowledgeChunkPayload,
        document: KnowledgeSourceDocument,
    ) -> StoredKnowledgeChunk:
        chunk = StoredKnowledgeChunk(
            id=uuid4(),
            source_id=source.id,
            source_version=source.version,
            chunk_index=chunk_payload.chunk_index,
            text=chunk_payload.text,
            content_hash=chunk_payload.content_hash,
            embedding=normalize_embedding(chunk_payload.embedding),
            source_metadata=source_metadata(
                source,
                document.source_metadata,
                document.citation_urls,
            ),
        )
        self.chunks[chunk.id] = chunk
        return chunk

    def _delete_chunks_for_source(self, source_id: UUID) -> None:
        for chunk_id, chunk in list(self.chunks.items()):
            if chunk.source_id == source_id:
                del self.chunks[chunk_id]


class SQLModelKnowledgeVectorStore:
    """Postgres-backed vector store using the `knowledge_chunk` table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_source(
        self,
        document: KnowledgeSourceDocument,
        chunks: list[KnowledgeChunkPayload],
        ingested_at: dt.datetime,
    ) -> IngestionResult:
        validate_document(document)
        _validate_chunks(chunks)
        key = source_key(document)
        active_sources = self._active_sources(key)
        existing_source = _existing_source_for_version(document.version, active_sources)
        if existing_source is not None:
            stored_source = _stored_source_from_model(existing_source)
            return IngestionResult(
                source=stored_source,
                chunks=self._chunks_for_source(existing_source.id),
                superseded_source_ids=[],
            )
        _reject_non_newer_version(document.version, active_sources)

        superseded_ids: list[UUID] = []
        for source in active_sources:
            source.superseded_at = ingested_at
            superseded_ids.append(source.id)
            self.session.add(source)

        source = KnowledgeSource(
            title=document.title,
            author_or_org=document.author_or_org,
            source_type=document.source_type,
            url_or_identifier=stored_source_identifier(document),
            license_status=document.license_status,
            published_at=document.published_at,
            ingested_at=ingested_at,
            version=document.version,
            trust_level=_required_trust_level(document),
        )
        self.session.add(source)
        self.session.flush()

        stored_source = _stored_source_from_model(source)
        stored_chunks: list[StoredKnowledgeChunk] = []
        for chunk_payload in chunks:
            chunk = KnowledgeChunk(
                source_id=source.id,
                source_version=source.version,
                chunk_index=chunk_payload.chunk_index,
                text=chunk_payload.text,
                content_hash=chunk_payload.content_hash,
                embedding=chunk_payload.embedding,
                source_metadata=source_metadata(
                    stored_source,
                    document.source_metadata,
                    document.citation_urls,
                ),
            )
            self.session.add(chunk)
            self.session.flush()
            stored_chunks.append(_stored_chunk_from_model(chunk))

        return IngestionResult(
            source=stored_source,
            chunks=stored_chunks,
            superseded_source_ids=superseded_ids,
        )

    def remove_source(self, source_identifier: str, removed_at: dt.datetime) -> int:
        removable_sources = self._removable_sources(source_identifier)
        for source in removable_sources:
            source.removed_at = removed_at
            self._delete_chunks_for_source(source.id)
            self.session.add(source)
        self.session.flush()
        return len(removable_sources)

    def _active_sources(self, source_identifier: str) -> list[KnowledgeSource]:
        normalized_identifier = source_key(source_identifier)
        statement = select(KnowledgeSource).where(
            col(KnowledgeSource.superseded_at).is_(None),
            col(KnowledgeSource.removed_at).is_(None),
        )
        return [
            source
            for source in self.session.exec(statement).all()
            if source.url_or_identifier is not None
            and source_key(source.url_or_identifier) == normalized_identifier
        ]

    def _removable_sources(self, source_identifier: str) -> list[KnowledgeSource]:
        normalized_identifier = source_key(source_identifier)
        statement = select(KnowledgeSource).where(col(KnowledgeSource.removed_at).is_(None))
        return [
            source
            for source in self.session.exec(statement).all()
            if source.url_or_identifier is not None
            and source_key(source.url_or_identifier) == normalized_identifier
        ]

    def _delete_chunks_for_source(self, source_id: UUID) -> None:
        self.session.execute(
            delete(KnowledgeChunk).where(col(KnowledgeChunk.source_id) == source_id)
        )

    def _chunks_for_source(self, source_id: UUID) -> list[StoredKnowledgeChunk]:
        statement = (
            select(KnowledgeChunk)
            .where(col(KnowledgeChunk.source_id) == source_id)
            .order_by(col(KnowledgeChunk.chunk_index))
        )
        return [_stored_chunk_from_model(chunk) for chunk in self.session.exec(statement).all()]


def _stored_source_from_model(source: KnowledgeSource) -> StoredKnowledgeSource:
    if (
        source.author_or_org is None
        or source.url_or_identifier is None
        or source.license_status is None
        or source.published_at is None
    ):
        raise ValueError("Stored knowledge sources must have full ingestion metadata")
    return StoredKnowledgeSource(
        id=source.id,
        title=source.title,
        author_or_org=source.author_or_org,
        source_type=source.source_type,
        url_or_identifier=source.url_or_identifier,
        license_status=source.license_status,
        published_at=source.published_at,
        ingested_at=source.ingested_at,
        version=source.version,
        trust_level=source.trust_level,
        superseded_at=source.superseded_at,
        removed_at=source.removed_at,
    )


def _required_trust_level(document: KnowledgeSourceDocument) -> TrustLevel:
    trust_level = document.trust_level
    if trust_level is None or trust_level not in ACCEPTED_TRUST_LEVELS:
        raise ValueError("Knowledge source trust_level must be validated before storage")
    return trust_level


def _validate_chunks(chunks: list[KnowledgeChunkPayload]) -> None:
    for chunk in chunks:
        validate_chunk_payload(chunk)
        normalize_embedding(chunk.embedding)


def _existing_source_for_version[KnowledgeSourceT: VersionedKnowledgeSource](
    version: str,
    active_sources: Sequence[KnowledgeSourceT],
) -> KnowledgeSourceT | None:
    for source in active_sources:
        if _compare_versions(version, source.version) == 0:
            return source
    return None


def _reject_non_newer_version(
    version: str,
    active_sources: Sequence[VersionedKnowledgeSource],
) -> None:
    newest_source = _newest_source(active_sources)
    if newest_source is not None and _compare_versions(version, newest_source.version) < 0:
        raise KnowledgeVersionError(
            "Knowledge source re-ingest must use a newer version than the active corpus"
        )


def _newest_source[KnowledgeSourceT: VersionedKnowledgeSource](
    active_sources: Sequence[KnowledgeSourceT],
) -> KnowledgeSourceT | None:
    if not active_sources:
        return None
    return max(active_sources, key=lambda source: _version_key(source.version))


def _compare_versions(incoming_version: str, active_version: str) -> int:
    incoming_key = _version_key(incoming_version)
    active_key = _version_key(active_version)
    if incoming_key == active_key:
        return 0
    return 1 if incoming_key > active_key else -1


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    normalized_version = version.strip().lower()
    tokens = VERSION_TOKEN_PATTERN.findall(normalized_version)
    if not tokens:
        return ((0, normalized_version),)
    return tuple((1, int(token)) if token.isdigit() else (0, token) for token in tokens)


def _stored_chunk_from_model(chunk: KnowledgeChunk) -> StoredKnowledgeChunk:
    return StoredKnowledgeChunk(
        id=chunk.id,
        source_id=chunk.source_id,
        source_version=chunk.source_version,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        content_hash=chunk.content_hash,
        embedding=chunk.embedding,
        source_metadata=chunk.source_metadata,
    )
