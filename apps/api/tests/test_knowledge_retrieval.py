"""External knowledge retrieval and citation-binding tests."""

from __future__ import annotations

import datetime as dt

import pytest
from packages.knowledge.models import KnowledgeSourceDocument
from packages.knowledge.pipeline import KnowledgeIngestionPipeline
from packages.knowledge.store import SQLModelKnowledgeVectorStore

from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel
from baseline_api.retrieval import (
    GENERAL_RESEARCH_LABEL,
    KnowledgeRetrievalService,
    bind_external_claims,
)

pytestmark = pytest.mark.require_db


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


def test_external_knowledge_retrieval_survives_committed_corpus_seed(db_session) -> None:
    _seed_corpus(db_session)
    db_session.commit()

    result = KnowledgeRetrievalService(db_session).retrieve("sleep recovery training readiness")

    assert result.citations
    assert result.citations[0].title == "General Sleep Recovery Reference"
    assert result.citation_accuracy >= 0.95


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


def test_external_retrieval_returns_no_sources_when_relevance_filter_fails(db_session) -> None:
    _seed_corpus(db_session)

    result = KnowledgeRetrievalService(db_session).retrieve("unrelated astrophysics telescope")

    assert result.hits == []
    assert result.citations == []
    assert "No relevant curated external source" in result.uncertainty[0]
