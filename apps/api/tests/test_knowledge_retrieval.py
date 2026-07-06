"""External knowledge retrieval and citation-binding tests."""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest
from packages.knowledge.models import KnowledgeSourceDocument
from packages.knowledge.pipeline import KnowledgeIngestionPipeline
from packages.knowledge.store import SQLModelKnowledgeVectorStore

from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.retrieval import (
    GENERAL_RESEARCH_LABEL,
    KnowledgeChunkHit,
    KnowledgeRetrievalService,
    bind_external_claims,
    build_external_knowledge_query,
    has_external_knowledge_consent,
)


def _document(
    *,
    title: str,
    content: str,
    url_or_identifier: str,
) -> KnowledgeSourceDocument:
    return KnowledgeSourceDocument(
        title=title,
        author_or_org="Baseline Test Curation Board",
        source_type=KnowledgeSourceType.guideline,
        url_or_identifier=url_or_identifier,
        license_status="CC0-1.0 public domain dedication",
        published_at=dt.date(2024, 1, 1),
        version="v1",
        trust_level=TrustLevel.authoritative,
        content=content,
    )


def _seed_corpus(db_session) -> None:
    pipeline = KnowledgeIngestionPipeline(SQLModelKnowledgeVectorStore(db_session))
    pipeline.ingest(
        _document(
            title="General Sleep Recovery Reference",
            url_or_identifier="https://example.org/general-sleep-recovery",
            content=(
                "General research on sleep recovery and training readiness describes broad "
                "non-personalized patterns for rest, recovery, and conservative training choices. "
                "This external source does not describe a Baseline user."
            ),
        )
    )
    pipeline.ingest(
        _document(
            title="General Nutrition Reference",
            url_or_identifier="https://example.org/general-nutrition",
            content=(
                "General nutrition references discuss broad public patterns for hydration and "
                "nutrient-dense foods. This external source does not describe a Baseline user."
            ),
        )
    )


@pytest.mark.require_db
def test_external_knowledge_retrieval_returns_relevant_cited_source(db_session) -> None:
    _seed_corpus(db_session)

    result = KnowledgeRetrievalService(db_session).retrieve("sleep recovery training readiness")

    assert result.hits
    assert result.hits[0].title == "General Sleep Recovery Reference"
    assert result.citations
    citation = result.citations[0]
    assert citation.title == "General Sleep Recovery Reference"
    assert citation.cited_claim.startswith(GENERAL_RESEARCH_LABEL)
    assert "non-personalized" in citation.cited_claim
    assert result.external_knowledge[0]["chunk_id"] == str(result.hits[0].chunk_id)
    assert result.citation_accuracy >= 0.95


@pytest.mark.require_db
def test_external_knowledge_retrieval_survives_committed_corpus_seed(db_session) -> None:
    _seed_corpus(db_session)
    db_session.commit()

    result = KnowledgeRetrievalService(db_session).retrieve("sleep recovery training readiness")

    assert result.citations
    assert result.citations[0].title == "General Sleep Recovery Reference"
    assert result.citation_accuracy >= 0.95


@pytest.mark.require_db
def test_unsupported_external_claim_is_suppressed_without_citation(db_session) -> None:
    _seed_corpus(db_session)
    result = KnowledgeRetrievalService(db_session).retrieve("sleep recovery training readiness")

    binding = bind_external_claims(
        [f"{GENERAL_RESEARCH_LABEL}Creatine cures anemia in Baseline users."],
        result.hits,
    )

    assert binding.citations == []
    assert binding.unsupported_claims == [
        f"{GENERAL_RESEARCH_LABEL}Creatine cures anemia in Baseline users."
    ]
    assert binding.citation_accuracy == 0.0


@pytest.mark.require_db
def test_external_retrieval_returns_no_sources_when_relevance_filter_fails(db_session) -> None:
    _seed_corpus(db_session)

    result = KnowledgeRetrievalService(db_session).retrieve("unrelated astrophysics telescope")

    assert result.hits == []
    assert result.citations == []
    assert "No relevant curated external source" in result.uncertainty[0]


def test_build_external_knowledge_query_includes_goals_and_band_without_raw_values() -> None:
    query = build_external_knowledge_query(
        active_goals=[
            {"category": "strength"},
            {"category": "sleep"},
        ],
        recommendation_band="easy",
        requested_scope="daily briefing",
    )

    assert "daily briefing" in query
    assert "strength training" in query
    assert "sleep debt" in query
    assert "easy training" in query
    assert "45.2" not in query
    assert "bpm" not in query
    assert "my note" not in query


def test_build_external_knowledge_query_extracts_allowed_question_topics_only() -> None:
    query = build_external_knowledge_query(
        active_goals=[{"category": "recovery"}],
        question="Why is my HRV low after the Barcelona race?",
        requested_scope="assistant wellness question",
    )

    assert "hrv" in query
    assert "recovery" in query
    assert "Barcelona" not in query
    assert "race" not in query
    assert "my" not in query


@pytest.mark.require_db
def test_external_retrieval_result_contains_no_personal_evidence(db_session) -> None:
    _seed_corpus(db_session)

    result = KnowledgeRetrievalService(db_session).retrieve("sleep recovery training readiness")

    assert result.hits
    assert all(
        citation.cited_claim.startswith(GENERAL_RESEARCH_LABEL) for citation in result.citations
    )
    assert all(
        "non-personalized" in hit.text or "general" in hit.text.lower() for hit in result.hits
    )


@pytest.mark.require_db
def test_external_knowledge_retrieval_skipped_when_consent_disabled(db_session) -> None:
    user = User(privacy_mode="cloud_assisted", active_consent_version="v1")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=["all"],
            cloud_processing_enabled=False,
            external_llm_enabled=False,
            raw_note_processing_enabled=False,
            timestamp=dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        )
    )
    db_session.commit()

    assert has_external_knowledge_consent(db_session, user.id) is False

    result = KnowledgeRetrievalService(db_session).retrieve("sleep recovery")

    assert result.hits == []
    assert result.citations == []
    assert result.external_knowledge == []


def test_citation_binding_suppresses_unsupported_medical_claim() -> None:
    hits = [
        KnowledgeChunkHit(
            chunk_id=uuid4(),
            source_id=uuid4(),
            source_version="v1",
            chunk_index=0,
            text="General research on recovery and sleep.",
            relevance_score=1.0,
            title="General Recovery Reference",
            source="Test Source",
            url_or_identifier="https://example.org/recovery",
            trust_level="authoritative",
        )
    ]
    claim = f"{GENERAL_RESEARCH_LABEL}Creatine cures anemia in Baseline users."

    binding = bind_external_claims([claim], hits)

    assert binding.citations == []
    assert binding.unsupported_claims == [claim]
    assert binding.citation_accuracy == 0.0
