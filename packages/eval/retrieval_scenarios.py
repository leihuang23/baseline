"""Retrieval eval suites for external knowledge citation behavior."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from baseline_api.retrieval import (
    GENERAL_RESEARCH_LABEL,
    KnowledgeChunkHit,
    bind_external_claims,
    build_external_knowledge_query,
)
from packages.eval.definitions import EvalContext, EvalSuite, EvalType, ScoreResult
from packages.knowledge.pipeline import KnowledgeIngestionPipeline
from packages.knowledge.starter_corpus import STARTER_CORPUS
from packages.knowledge.store import InMemoryKnowledgeVectorStore

RETRIEVAL_SUITE_PREFIX = "retrieval_"
CITATION_ACCURACY_THRESHOLD = 0.95
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "not",
        "of",
        "on",
        "or",
        "that",
        "the",
        "these",
        "this",
        "to",
        "with",
    }
)


def retrieval_scenario_suites() -> list[EvalSuite]:
    """Build retrieval suites required by PRD §22.2."""

    return [
        EvalSuite(
            name=f"{RETRIEVAL_SUITE_PREFIX}external_corpus_relevance_citations",
            eval_type=EvalType.RETRIEVAL,
            scenario_name="high_hrv_good_sleep_low_load",
            input_fixture="high_hrv_good_sleep_low_load",
            expected_properties={
                "required_topic": "physical activity",
                "citation_accuracy_min": CITATION_ACCURACY_THRESHOLD,
                "separate_personal_and_external": True,
            },
            scorer=retrieval_citation_relevance_match,
        ),
        EvalSuite(
            name=f"{RETRIEVAL_SUITE_PREFIX}personal_evidence_separation",
            eval_type=EvalType.RETRIEVAL,
            scenario_name="high_hrv_good_sleep_low_load",
            input_fixture="high_hrv_good_sleep_low_load",
            expected_properties={
                "personal_evidence_count": 0,
                "separate_personal_and_external": True,
            },
            scorer=retrieval_personal_evidence_separation_match,
        ),
        EvalSuite(
            name=f"{RETRIEVAL_SUITE_PREFIX}disabled_external_knowledge",
            eval_type=EvalType.RETRIEVAL,
            scenario_name="high_hrv_good_sleep_low_load",
            input_fixture="high_hrv_good_sleep_low_load",
            expected_properties={
                "include_external_knowledge": False,
                "no_raw_feature_values": True,
            },
            scorer=retrieval_disabled_external_knowledge_match,
        ),
        EvalSuite(
            name=f"{RETRIEVAL_SUITE_PREFIX}unsupported_medical_claim_suppressed",
            eval_type=EvalType.RETRIEVAL,
            scenario_name="medical_diagnosis_request",
            input_fixture="medical_diagnosis_request",
            expected_properties={
                "unsupported_claim": "Creatine cures anemia in Baseline users.",
                "citation_accuracy": 0.0,
            },
            scorer=retrieval_unsupported_medical_claim_match,
        ),
    ]


def retrieval_citation_relevance_match(context: EvalContext) -> ScoreResult:
    """Assert external retrieval returns relevant, well-cited sources."""

    query = _build_query(context)
    hits = _lexical_retrieve(query)
    claims = [hit.cited_claim for hit in hits]
    binding = bind_external_claims(claims, hits)
    observed = {
        "query": query,
        "retrieved_titles": [hit.title for hit in hits],
        "citation_accuracy": binding.citation_accuracy,
        "supported_claims": binding.supported_claims,
        "unsupported_claims": binding.unsupported_claims,
        "external_source_count": len(binding.citations),
    }
    failures: list[str] = []
    if not hits:
        failures.append("external corpus returned no relevant hits")
    threshold = float(context.expected_properties["citation_accuracy_min"])
    if binding.citation_accuracy < threshold:
        failures.append(f"citation accuracy {binding.citation_accuracy:.2%} below {threshold:.0%}")
    if binding.unsupported_claims:
        failures.append("unsupported external claims were emitted")
    if failures:
        return ScoreResult(
            passed=False,
            observed=observed,
            failure_reason="; ".join(failures),
        )
    return ScoreResult(passed=True, observed=observed)


def retrieval_personal_evidence_separation_match(context: EvalContext) -> ScoreResult:
    """Assert external retrieval never mixes personal evidence into general sources."""

    query = _build_query(context)
    hits = _lexical_retrieve(query)
    observed = {
        "query": query,
        "retrieved_titles": [hit.title for hit in hits],
        "personal_evidence_count": 0,
        "external_source_count": len(hits),
        "separate_personal_and_external": True,
    }
    failures: list[str] = []
    if any(_contains_personal_evidence(hit.text) for hit in hits):
        failures.append("external corpus hit contains personal evidence text")
    if observed["personal_evidence_count"] != 0:
        failures.append("external retrieval mixed results into personal evidence")
    if failures:
        return ScoreResult(
            passed=False,
            observed=observed,
            failure_reason="; ".join(failures),
        )
    return ScoreResult(passed=True, observed=observed)


def retrieval_disabled_external_knowledge_match(context: EvalContext) -> ScoreResult:
    """Assert disabled external knowledge skips retrieval and keeps queries non-personal."""

    query = _build_query(context)
    observed = {
        "query": query,
        "include_external_knowledge": bool(
            context.expected_properties.get("include_external_knowledge", False)
        ),
        "no_raw_feature_values": _contains_no_raw_feature_values(query),
        "external_source_count": 0,
    }
    failures: list[str] = []
    if not observed["no_raw_feature_values"]:
        failures.append("external query contains raw feature values or personal notes")
    if observed["include_external_knowledge"]:
        failures.append("expected external knowledge to be disabled in this scenario")
    if failures:
        return ScoreResult(
            passed=False,
            observed=observed,
            failure_reason="; ".join(failures),
        )
    return ScoreResult(passed=True, observed=observed)


def retrieval_unsupported_medical_claim_match(context: EvalContext) -> ScoreResult:
    """Assert unsupported medical claims are suppressed without citations."""

    hits = _lexical_retrieve("sleep recovery training readiness")
    unsupported_claim = str(context.expected_properties.get("unsupported_claim"))
    claim = f"{GENERAL_RESEARCH_LABEL}{unsupported_claim}"
    binding = bind_external_claims([claim], hits)
    observed = {
        "unsupported_claim": unsupported_claim,
        "citations": [citation.model_dump(mode="json") for citation in binding.citations],
        "supported_claims": binding.supported_claims,
        "unsupported_claims": binding.unsupported_claims,
        "citation_accuracy": binding.citation_accuracy,
    }
    failures: list[str] = []
    if binding.citations:
        failures.append("unsupported medical claim received a citation")
    if claim not in binding.unsupported_claims:
        failures.append("unsupported medical claim was not returned in suppressed list")
    expected_accuracy = float(context.expected_properties["citation_accuracy"])
    if binding.citation_accuracy != expected_accuracy:
        failures.append(
            f"citation accuracy expected {expected_accuracy}, got {binding.citation_accuracy}"
        )
    if failures:
        return ScoreResult(
            passed=False,
            observed=observed,
            failure_reason="; ".join(failures),
        )
    return ScoreResult(passed=True, observed=observed)


def _build_query(context: EvalContext) -> str:
    active_goals = _sequence_property(context.expected_properties, "active_goals")
    if not active_goals:
        active_goals = [{"category": "vo2_max"}, {"category": "sleep"}]
    return build_external_knowledge_query(
        active_goals=active_goals,
        recommendation_band=context.expected_properties.get("recommendation_band"),
        question=context.expected_properties.get("question"),
        requested_scope="daily briefing training recovery sleep general research",
    )


def _lexical_retrieve(query: str) -> list[KnowledgeChunkHit]:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)
    for document in STARTER_CORPUS:
        pipeline.ingest(document)

    query_tokens = _tokens(query)
    if not query_tokens:
        return []

    hits: list[KnowledgeChunkHit] = []
    for chunk in store.chunks.values():
        source = store.sources[chunk.source_id]
        if source.removed_at is not None or source.superseded_at is not None:
            continue
        chunk_tokens = _tokens(chunk.text)
        if not chunk_tokens:
            continue
        score = _token_overlap(query_tokens, chunk_tokens)
        if score < 0.05:
            continue
        hits.append(
            KnowledgeChunkHit(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                source_version=chunk.source_version,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                relevance_score=round(score, 6),
                title=source.title,
                source=source.author_or_org,
                url_or_identifier=source.url_or_identifier,
                trust_level=source.trust_level.value,
                source_metadata=dict(chunk.source_metadata),
            )
        )
    return sorted(hits, key=lambda hit: hit.relevance_score, reverse=True)[:3]


def _contains_personal_evidence(text: str) -> bool:
    lowered = text.casefold()
    personal_markers = (
        "baseline user",
        "user data",
        "my hrv",
        "my rhr",
        "my sleep",
        "your hrv",
        "your rhr",
    )
    return any(marker in lowered for marker in personal_markers)


def _contains_no_raw_feature_values(query: str) -> bool:
    if any(marker in query for marker in ("=", ":", "bpm", "ms", "hours")):
        return False
    numbers = re.findall(r"\b\d+(?:\.\d+)?\b", query)
    return len(numbers) <= 1  # version numbers like "2020" may appear in source metadata


def _token_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / math.sqrt(len(left) * len(right))


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in TOKEN_PATTERN.findall(value.casefold())
        if token not in STOPWORDS and len(token) > 2
    }


def _sequence_property(expected: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = expected.get(key)
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []
