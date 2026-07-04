"""Curated external knowledge corpus ingestion tests."""

import datetime as dt
from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest
from packages.knowledge.chunking import ChunkingConfig
from packages.knowledge.curation import CurationError, PersonalDataBoundaryError
from packages.knowledge.models import (
    KnowledgeChunkPayload,
    KnowledgeSourceDocument,
)
from packages.knowledge.pipeline import KnowledgeIngestionPipeline
from packages.knowledge.starter_corpus import STARTER_CORPUS
from packages.knowledge.store import (
    InMemoryKnowledgeVectorStore,
    KnowledgeVersionError,
    SQLModelKnowledgeVectorStore,
)
from sqlmodel import Session, select

from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel
from baseline_api.db.models.knowledge import (
    KNOWLEDGE_EMBEDDING_DIMENSION,
    KnowledgeChunk,
    KnowledgeSource,
)
from baseline_api.db.repositories.knowledge import (
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
)


def _document(
    *,
    version: str = "v1",
    trust_level: TrustLevel | None = TrustLevel.authoritative,
    source_type: KnowledgeSourceType = KnowledgeSourceType.guideline,
    url_or_identifier: str = "https://example.org/recovery-reference",
    published_at: dt.date | None = dt.date(2024, 1, 1),
    citation_urls: tuple[str, ...] = (),
    source_metadata: dict[str, object] | None = None,
    content: str | None = None,
) -> KnowledgeSourceDocument:
    return KnowledgeSourceDocument(
        title="General Recovery Reference",
        author_or_org="Baseline Test Curation Board",
        source_type=source_type,
        url_or_identifier=url_or_identifier,
        license_status="CC0-1.0 public domain dedication",
        published_at=published_at,
        version=version,
        trust_level=trust_level,
        citation_urls=citation_urls,
        source_metadata=source_metadata or {},
        content=content
        or (
            "General recovery references discuss broad, non-personal patterns in sleep, "
            "training load, and rest. These statements are external background evidence "
            "only and do not describe a Baseline user. " * 6
        ),
    )


def _chunk_payloads(text: str = "External reference chunk.") -> list[KnowledgeChunkPayload]:
    return [
        KnowledgeChunkPayload(
            chunk_index=0,
            text=text,
            content_hash=f"hash-{text}",
            embedding=[0.1] * KNOWLEDGE_EMBEDDING_DIMENSION,
        )
    ]


def test_ingestion_stores_full_source_metadata_on_each_chunk() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(
        store,
        chunking_config=ChunkingConfig(max_chars=240, overlap_chars=30),
    )

    result = pipeline.ingest(
        _document(
            version="v1",
            citation_urls=("https://example.org/citations/recovery",),
            source_metadata={
                "doi": "10.0000/baseline-test",
                "publisher": "Baseline Test Publisher",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            },
        )
    )

    assert result.source.title == "General Recovery Reference"
    assert result.source.trust_level is TrustLevel.authoritative
    assert len(result.chunks) >= 2
    for chunk in result.chunks:
        assert chunk.source_id == result.source.id
        assert chunk.source_version == "v1"
        assert chunk.source_metadata["source_id"] == str(result.source.id)
        assert chunk.source_metadata["title"] == "General Recovery Reference"
        assert chunk.source_metadata["author_or_org"] == "Baseline Test Curation Board"
        assert chunk.source_metadata["source_type"] == "guideline"
        assert chunk.source_metadata["url_or_identifier"] == (
            "https://example.org/recovery-reference"
        )
        assert chunk.source_metadata["license_status"] == "CC0-1.0 public domain dedication"
        assert chunk.source_metadata["published_at"] == "2024-01-01"
        assert chunk.source_metadata["version"] == "v1"
        assert chunk.source_metadata["trust_level"] == "authoritative"
        assert chunk.source_metadata["doi"] == "10.0000/baseline-test"
        assert chunk.source_metadata["publisher"] == "Baseline Test Publisher"
        assert chunk.source_metadata["license_url"] == (
            "https://creativecommons.org/publicdomain/zero/1.0/"
        )
        assert chunk.source_metadata["citation_urls"] == ["https://example.org/citations/recovery"]
        assert len(chunk.embedding) == 16
        assert any(value != 0 for value in chunk.embedding)


def test_curation_rejects_untrusted_or_uncited_web_content_before_storage() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    with pytest.raises(CurationError, match="explicit"):
        pipeline.ingest(_document(trust_level=TrustLevel.unverified))

    with pytest.raises(CurationError, match="supporting citations"):
        pipeline.ingest(
            _document(
                trust_level=TrustLevel.curated,
                source_type=KnowledgeSourceType.article,
                citation_urls=(),
            )
        )

    assert store.sources == {}
    assert store.chunks == {}


def test_curation_rejects_article_with_only_blank_citation_entries_before_storage() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    with pytest.raises(CurationError, match="supporting citations"):
        pipeline.ingest(
            _document(
                trust_level=TrustLevel.curated,
                source_type=KnowledgeSourceType.article,
                citation_urls=("", "   "),
            )
        )

    assert store.sources == {}
    assert store.chunks == {}


def test_curation_requires_published_at_before_storage() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    with pytest.raises(CurationError, match="published_at"):
        pipeline.ingest(_document(published_at=None))

    assert store.sources == {}
    assert store.chunks == {}


def test_curation_requires_source_type_enum_before_storage() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    with pytest.raises(CurationError, match="source_type"):
        pipeline.ingest(
            replace(
                _document(),
                source_type=cast(KnowledgeSourceType, None),
            )
        )

    with pytest.raises(CurationError, match="KnowledgeSourceType"):
        pipeline.ingest(
            replace(
                _document(),
                source_type=cast(KnowledgeSourceType, "article"),
            )
        )

    assert store.sources == {}
    assert store.chunks == {}


def test_reingest_supersedes_prior_version_and_retains_old_chunks_for_audit() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    first = pipeline.ingest(_document(version="v1", content="Version one external text. " * 20))
    second = pipeline.ingest(_document(version="v2", content="Version two external text. " * 20))

    assert second.superseded_source_ids == [first.source.id]
    assert store.sources[first.source.id].superseded_at is not None
    assert store.chunks_for_source(first.source.id) == first.chunks
    assert store.chunks_for_source(second.source.id) == second.chunks
    assert all(chunk.source_metadata["version"] == "v1" for chunk in first.chunks)
    assert all(chunk.source_metadata["version"] == "v2" for chunk in second.chunks)


def test_same_version_reingest_is_idempotent_and_preserves_active_chunks() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    first = pipeline.ingest(_document(version="v1", content="Original external text. " * 20))
    same = pipeline.ingest(_document(version="v1", content="Changed external text. " * 20))

    assert same.source.id == first.source.id
    assert same.superseded_source_ids == []
    assert same.chunks == first.chunks
    assert len(store.sources) == 1
    assert store.chunks_for_source(first.source.id) == first.chunks
    assert all("Original external text" in chunk.text for chunk in same.chunks)


def test_older_version_reingest_is_rejected_without_mutating_active_chunks() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    active = pipeline.ingest(_document(version="v2", content="Current external text. " * 20))

    with pytest.raises(KnowledgeVersionError, match="newer version"):
        pipeline.ingest(_document(version="v1", content="Older external text. " * 20))

    assert len(store.sources) == 1
    assert store.sources[active.source.id].superseded_at is None
    assert store.chunks_for_source(active.source.id) == active.chunks


def test_removal_marks_active_source_and_purges_chunks() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    result = pipeline.ingest(_document())

    assert pipeline.remove("https://example.org/recovery-reference") == 1
    assert store.sources[result.source.id].removed_at is not None
    assert store.chunks_for_source(result.source.id) == []


def test_removal_after_supersession_purges_all_matching_source_chunks() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    first = pipeline.ingest(_document(version="v1", content="Version one external text. " * 20))
    second = pipeline.ingest(_document(version="v2", content="Version two external text. " * 20))

    assert pipeline.remove("https://example.org/recovery-reference") == 2
    assert store.sources[first.source.id].removed_at is not None
    assert store.sources[second.source.id].removed_at is not None
    assert store.chunks_for_source(first.source.id) == []
    assert store.chunks_for_source(second.source.id) == []


def test_personal_data_boundary_rejects_health_provenance() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    with pytest.raises(PersonalDataBoundaryError):
        pipeline.ingest(
            _document(
                source_metadata={
                    "user_id": "063678c1-7a1e-4b29-8c1c-486d38ca6316",
                    "source_sample_ids": ["hk-sample-1"],
                }
            )
        )

    assert store.sources == {}
    assert store.chunks == {}


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("title", "Apple Health provenance marker"),
        ("author_or_org", "HealthKit reference marker"),
        ("url_or_identifier", "https://example.org/source_sample_ids"),
        ("license_status", "raw_health_sample provenance marker"),
        ("version", "model_run_id-v1"),
    ],
)
def test_personal_data_boundary_rejects_internal_provenance_in_source_fields(
    field_name: str,
    value: str,
) -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    with pytest.raises(PersonalDataBoundaryError):
        pipeline.ingest(replace(_document(), **{field_name: value}))

    assert store.sources == {}
    assert store.chunks == {}


def test_personal_data_boundary_rejects_internal_provenance_in_citation_urls() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    with pytest.raises(PersonalDataBoundaryError):
        pipeline.ingest(
            _document(
                source_type=KnowledgeSourceType.article,
                citation_urls=("https://example.org/source_sample_ids",),
            )
        )

    assert store.sources == {}
    assert store.chunks == {}


@pytest.mark.parametrize(
    "source_metadata",
    [
        {"prompt_payload": "<redacted>"},
        {"model_run_id": "5f4a5cb5-7508-4b65-bd28-b8bcf999866e"},
        {"healthKitSampleID": "hk-sample-1"},
        {"manual_check_in_id": "daily-check-in-1"},
        {"freeTextNote": "<redacted>"},
    ],
)
def test_personal_data_boundary_rejects_internal_provenance_variants(
    source_metadata: dict[str, object],
) -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    with pytest.raises(PersonalDataBoundaryError):
        pipeline.ingest(_document(source_metadata=source_metadata))

    assert store.sources == {}
    assert store.chunks == {}


def test_direct_vector_store_upserts_reject_untrusted_or_uncited_documents() -> None:
    fake_session = cast(Session, object())
    stores = [
        InMemoryKnowledgeVectorStore(),
        SQLModelKnowledgeVectorStore(fake_session),
    ]

    for store in stores:
        with pytest.raises(CurationError, match="explicit"):
            store.upsert_source(
                _document(trust_level=TrustLevel.unverified),
                _chunk_payloads(),
                dt.datetime(2024, 1, 2, tzinfo=dt.UTC),
            )

        with pytest.raises(CurationError, match="supporting citations"):
            store.upsert_source(
                _document(
                    trust_level=TrustLevel.curated,
                    source_type=KnowledgeSourceType.article,
                    citation_urls=(),
                ),
                _chunk_payloads(),
                dt.datetime(2024, 1, 2, tzinfo=dt.UTC),
            )

    assert stores[0].sources == {}
    assert stores[0].chunks == {}


def test_direct_vector_store_upserts_reject_personal_data_chunks() -> None:
    fake_session = cast(Session, object())
    stores = [
        InMemoryKnowledgeVectorStore(),
        SQLModelKnowledgeVectorStore(fake_session),
    ]

    for store in stores:
        with pytest.raises(PersonalDataBoundaryError, match="chunks"):
            store.upsert_source(
                _document(),
                _chunk_payloads("Apple Health source_sample_ids belong outside the corpus."),
                dt.datetime(2024, 1, 2, tzinfo=dt.UTC),
            )

    assert stores[0].sources == {}
    assert stores[0].chunks == {}


def test_direct_vector_store_upserts_reject_wrong_embedding_dimension() -> None:
    fake_session = cast(Session, object())
    stores = [
        InMemoryKnowledgeVectorStore(),
        SQLModelKnowledgeVectorStore(fake_session),
    ]

    for store in stores:
        with pytest.raises(ValueError, match="16 dimensions"):
            store.upsert_source(
                _document(),
                [
                    KnowledgeChunkPayload(
                        chunk_index=0,
                        text="External reference text.",
                        content_hash="wrong-dimension",
                        embedding=[0.1, 0.2, 0.3],
                    )
                ],
                dt.datetime(2024, 1, 2, tzinfo=dt.UTC),
            )

    assert stores[0].sources == {}
    assert stores[0].chunks == {}


def test_knowledge_repositories_reject_raw_writes() -> None:
    fake_session = cast(Session, object())
    source_repo = KnowledgeSourceRepository(fake_session)
    chunk_repo = KnowledgeChunkRepository(fake_session)
    source = KnowledgeSource(
        title="Uncurated Source",
        author_or_org="Unknown",
        source_type=KnowledgeSourceType.article,
        url_or_identifier="https://example.org/uncited",
        license_status="unknown",
        published_at=dt.date(2024, 1, 1),
        ingested_at=dt.datetime(2024, 1, 2, tzinfo=dt.UTC),
        version="draft",
        trust_level=TrustLevel.unverified,
    )

    with pytest.raises(CurationError, match="ingestion pipeline"):
        source_repo.create(source)

    with pytest.raises(CurationError, match="ingestion pipeline"):
        chunk_repo.create(
            KnowledgeChunk(
                source_id=source.id,
                source_version=source.version,
                chunk_index=0,
                text="Raw chunk.",
                content_hash="raw-chunk",
                embedding=[0.1] * KNOWLEDGE_EMBEDDING_DIMENSION,
                source_metadata={},
            )
        )


def test_knowledge_chunk_model_rejects_wrong_embedding_dimension() -> None:
    with pytest.raises(ValueError, match="16 dimensions"):
        KnowledgeChunk(
            source_id=uuid4(),
            source_version="v1",
            chunk_index=0,
            text="External reference text.",
            content_hash="bad-dimension",
            embedding=[0.1, 0.2, 0.3],
            source_metadata={},
        )


def test_sql_store_preserves_identifier_while_matching_for_supersede_and_removal(
    db_session,
) -> None:
    store = SQLModelKnowledgeVectorStore(db_session)
    first_ingested_at = dt.datetime(2024, 1, 2, tzinfo=dt.UTC)
    second_ingested_at = dt.datetime(2024, 1, 3, tzinfo=dt.UTC)
    removed_at = dt.datetime(2024, 1, 4, tzinfo=dt.UTC)

    first = store.upsert_source(
        _document(url_or_identifier=" HTTPS://Example.ORG/Recovery-Reference  "),
        _chunk_payloads("First version chunk."),
        first_ingested_at,
    )
    second = store.upsert_source(
        _document(version="v2", url_or_identifier="https://example.org/recovery-reference"),
        _chunk_payloads("Second version chunk."),
        second_ingested_at,
    )

    stored_first = db_session.get(KnowledgeSource, first.source.id)
    stored_second = db_session.get(KnowledgeSource, second.source.id)
    assert stored_first is not None
    assert stored_second is not None
    assert stored_first.url_or_identifier == "HTTPS://Example.ORG/Recovery-Reference"
    assert stored_second.url_or_identifier == "https://example.org/recovery-reference"
    assert second.superseded_source_ids == [first.source.id]
    assert stored_first.superseded_at == second_ingested_at
    assert [
        chunk.text
        for chunk in db_session.exec(
            select(KnowledgeChunk).where(KnowledgeChunk.source_id == first.source.id)
        ).all()
    ] == ["First version chunk."]

    assert store.remove_source(" https://example.org/recovery-reference ", removed_at) == 2
    assert stored_first.removed_at == removed_at
    assert stored_second.removed_at == removed_at
    assert (
        db_session.exec(
            select(KnowledgeChunk).where(KnowledgeChunk.source_id == first.source.id)
        ).all()
        == []
    )
    assert (
        db_session.exec(
            select(KnowledgeChunk).where(KnowledgeChunk.source_id == second.source.id)
        ).all()
        == []
    )


def test_sql_store_rejects_downgrade_and_noops_same_version(db_session) -> None:
    store = SQLModelKnowledgeVectorStore(db_session)
    ingested_at = dt.datetime(2024, 1, 2, tzinfo=dt.UTC)

    active = store.upsert_source(
        _document(version="v2", content="Current SQL external text. " * 20),
        _chunk_payloads("Current SQL chunk."),
        ingested_at,
    )
    same = store.upsert_source(
        _document(version="v2", content="Changed SQL external text. " * 20),
        _chunk_payloads("Changed SQL chunk."),
        dt.datetime(2024, 1, 3, tzinfo=dt.UTC),
    )

    assert same.source.id == active.source.id
    assert same.superseded_source_ids == []
    assert [chunk.text for chunk in same.chunks] == ["Current SQL chunk."]

    with pytest.raises(KnowledgeVersionError, match="newer version"):
        store.upsert_source(
            _document(version="v1", content="Older SQL external text. " * 20),
            _chunk_payloads("Older SQL chunk."),
            dt.datetime(2024, 1, 4, tzinfo=dt.UTC),
        )

    stored_sources = db_session.exec(select(KnowledgeSource)).all()
    assert [source.id for source in stored_sources] == [active.source.id]
    stored_chunks = db_session.exec(select(KnowledgeChunk)).all()
    assert [chunk.text for chunk in stored_chunks] == ["Current SQL chunk."]


def test_starter_corpus_is_license_clear_and_ingestible() -> None:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)

    for document in STARTER_CORPUS:
        assert "public domain" in document.license_status.lower()
        result = pipeline.ingest(document)
        assert result.source.trust_level is TrustLevel.authoritative
        assert result.chunks
