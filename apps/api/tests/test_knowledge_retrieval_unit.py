"""Unit-level regression tests for external knowledge retrieval."""

from __future__ import annotations

import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from packages.knowledge.embeddings import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    HTTPEmbeddingProvider,
)
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


def test_external_retrieval_marks_embedding_failure_fallback_degraded() -> None:
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
        embedding=HashEmbeddingProvider().embed(chunk_text),
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

    class FailingEmbedder:
        def embed(self, text: str) -> list[float]:
            _ = text
            raise TimeoutError("provider timeout")

    result = KnowledgeRetrievalService(
        cast(Session, _FakeSession([chunk], {source_id: source})),
        embedder=cast(EmbeddingProvider, FailingEmbedder()),
    ).retrieve("daily readiness training recovery sleep general research")

    assert result.degraded is True
    assert result.degrade_reason == "TimeoutError"
    assert [citation.title for citation in result.citations] == [
        "General Training Recovery Reference"
    ]


def test_http_embedding_provider_disables_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_handlers: list[Any] = []

    class RedirectingOpener:
        def open(
            self,
            request: urllib.request.Request,
            *,
            timeout: float,
        ) -> object:
            _ = (request, timeout)
            raise urllib.error.HTTPError(
                "https://embeddings.example/v1",
                302,
                "Found",
                {},
                None,
            )

    def fake_build_opener(*handlers: Any) -> RedirectingOpener:
        captured_handlers.extend(handlers)
        return RedirectingOpener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    provider = HTTPEmbeddingProvider(
        api_url="https://embeddings.example/v1",
        api_key="secret-token",
        model="test-embedding",
        max_retries=0,
    )

    with pytest.raises(urllib.error.HTTPError):
        provider.embed("sleep recovery")

    assert captured_handlers
    first_handler = captured_handlers[0]
    handler = first_handler() if isinstance(first_handler, type) else first_handler
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://other.example") is None
