"""Unit-level regression tests for external knowledge retrieval."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from packages.knowledge.embeddings import HashEmbeddingProvider
from sqlmodel import Session

from baseline_api.db.models.enums import TrustLevel
from baseline_api.db.models.knowledge import KnowledgeChunk
from baseline_api.retrieval import KnowledgeRetrievalService


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, chunks: list[Any], sources: dict[Any, Any]) -> None:
        self._chunks = chunks
        self._sources = sources

    def exec(self, _statement: Any) -> _Rows:
        return _Rows(self._chunks)

    def get(self, _model: Any, source_id: Any) -> Any:
        return self._sources.get(source_id)


def test_external_retrieval_skips_bad_rows_without_losing_valid_citations() -> None:
    embedder = HashEmbeddingProvider()
    query = "daily readiness training recovery sleep general research"
    bad_source_id = uuid4()
    bad_metadata_source_id = uuid4()
    good_source_id = uuid4()
    bad_text = "General research on daily readiness and recovery."
    bad_metadata_text = "General research on daily readiness and training recovery."
    good_text = (
        "General research on daily readiness, training recovery, and sleep describes broad "
        "non-personalized patterns for conservative training choices."
    )
    chunks = [
        KnowledgeChunk(
            source_id=bad_source_id,
            source_version="v1",
            chunk_index=0,
            text=bad_text,
            content_hash="bad-row",
            embedding=embedder.embed(bad_text),
            source_metadata={},
        ),
        SimpleNamespace(
            source_id=bad_metadata_source_id,
            source_version="v1",
            chunk_index=1,
            text=bad_metadata_text,
            content_hash="bad-metadata-row",
            embedding=embedder.embed(bad_metadata_text),
            source_metadata=[],
        ),
        KnowledgeChunk(
            source_id=good_source_id,
            source_version="v1",
            chunk_index=2,
            text=good_text,
            content_hash="good-row",
            embedding=embedder.embed(good_text),
            source_metadata={},
        ),
    ]
    sources = {
        bad_source_id: SimpleNamespace(
            superseded_at=None,
            removed_at=None,
            trust_level=None,
        ),
        bad_metadata_source_id: SimpleNamespace(
            title="Malformed Metadata Reference",
            author_or_org="Baseline Test Curation Board",
            url_or_identifier="https://example.org/malformed-metadata",
            trust_level=TrustLevel.authoritative,
            superseded_at=None,
            removed_at=None,
        ),
        good_source_id: SimpleNamespace(
            title="General Training Recovery Reference",
            author_or_org="Baseline Test Curation Board",
            url_or_identifier="https://example.org/general-training-recovery",
            trust_level=TrustLevel.authoritative,
            superseded_at=None,
            removed_at=None,
        ),
    }

    result = KnowledgeRetrievalService(cast(Session, _FakeSession(chunks, sources))).retrieve(query)

    assert result.degraded is False
    assert [citation.title for citation in result.citations] == [
        "General Training Recovery Reference"
    ]
    assert result.citation_accuracy >= 0.95


def test_external_retrieval_treats_missing_chunk_metadata_as_optional() -> None:
    source_id = uuid4()
    chunk_text = (
        "General research on daily readiness, training recovery, and sleep describes broad "
        "non-personalized patterns for conservative training choices."
    )
    chunk = SimpleNamespace(
        id=uuid4(),
        source_id=source_id,
        source_version="v1",
        chunk_index=0,
        text=chunk_text,
        content_hash="missing-metadata-row",
        embedding=HashEmbeddingProvider().embed(chunk_text),
        source_metadata=None,
    )
    source = SimpleNamespace(
        title="General Training Recovery Reference",
        author_or_org="Baseline Test Curation Board",
        url_or_identifier="https://example.org/general-training-recovery",
        trust_level=TrustLevel.authoritative,
        superseded_at=None,
        removed_at=None,
    )

    result = KnowledgeRetrievalService(
        cast(Session, _FakeSession([chunk], {source_id: source}))
    ).retrieve("daily readiness training recovery sleep general research")

    assert [citation.title for citation in result.citations] == [
        "General Training Recovery Reference"
    ]
    assert "General research (non-personalized)" in result.citations[0].cited_claim
    assert result.citation_accuracy >= 0.95


def test_external_retrieval_uses_lexical_fallback_when_embedding_is_unusable() -> None:
    source_id = uuid4()
    chunk_text = (
        "General research on daily readiness, training recovery, and sleep describes broad "
        "non-personalized patterns for conservative training choices."
    )
    chunk = SimpleNamespace(
        id=uuid4(),
        source_id=source_id,
        source_version="v1",
        chunk_index=0,
        text=chunk_text,
        content_hash="fallback-row",
        embedding=None,
        source_metadata={},
    )
    source = SimpleNamespace(
        title="General Training Recovery Reference",
        author_or_org="Baseline Test Curation Board",
        url_or_identifier="https://example.org/general-training-recovery",
        trust_level=TrustLevel.authoritative,
        superseded_at=None,
        removed_at=None,
    )

    result = KnowledgeRetrievalService(
        cast(Session, _FakeSession([chunk], {source_id: source}))
    ).retrieve("daily readiness training recovery sleep general research")

    assert result.degraded is False
    assert [citation.title for citation in result.citations] == [
        "General Training Recovery Reference"
    ]
    assert "General research (non-personalized)" in result.citations[0].cited_claim
    assert result.external_knowledge[0]["chunk_id"] == str(chunk.id)
    assert result.citation_accuracy >= 0.95
