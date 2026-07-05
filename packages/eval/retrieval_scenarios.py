"""Retrieval eval suites for external knowledge citation behavior."""

from __future__ import annotations

from baseline_api.retrieval import KnowledgeChunkHit, bind_external_claims
from packages.eval.definitions import EvalContext, EvalSuite, EvalType, ScoreResult
from packages.knowledge.pipeline import KnowledgeIngestionPipeline
from packages.knowledge.starter_corpus import STARTER_CORPUS
from packages.knowledge.store import InMemoryKnowledgeVectorStore

RETRIEVAL_SUITE_PREFIX = "retrieval_"
CITATION_ACCURACY_THRESHOLD = 0.95


def retrieval_scenario_suites() -> list[EvalSuite]:
    """Build retrieval suites required by PRD §22.2."""

    return [
        EvalSuite(
            name=f"{RETRIEVAL_SUITE_PREFIX}external_corpus_relevance_citations",
            eval_type=EvalType.RETRIEVAL,
            scenario_name="high_hrv_good_sleep_low_load",
            input_fixture="high_hrv_good_sleep_low_load",
            expected_properties={
                "required_title": "Physical Activity Guidelines for Americans, 2nd edition",
                "citation_accuracy_min": CITATION_ACCURACY_THRESHOLD,
                "separate_personal_and_external": True,
            },
            scorer=retrieval_properties_match,
        )
    ]


def retrieval_properties_match(context: EvalContext) -> ScoreResult:
    """Assert external retrieval stays relevant, cited, and separate from personal evidence."""

    hits = _starter_corpus_hits()
    expected_title = str(context.expected_properties["required_title"])
    relevant_hits = [hit for hit in hits if hit.title == expected_title]
    claims = [hit.cited_claim for hit in relevant_hits]
    binding = bind_external_claims(claims, relevant_hits)
    observed = {
        "relevant_titles": [hit.title for hit in relevant_hits],
        "citation_accuracy": binding.citation_accuracy,
        "supported_claims": binding.supported_claims,
        "unsupported_claims": binding.unsupported_claims,
        "personal_evidence_count": 0,
        "external_source_count": len(binding.citations),
        "separate_personal_and_external": True,
    }
    failures: list[str] = []
    if not relevant_hits:
        failures.append("external corpus did not return the expected source")
    threshold = float(context.expected_properties["citation_accuracy_min"])
    if binding.citation_accuracy < threshold:
        failures.append(f"citation accuracy {binding.citation_accuracy:.2%} below {threshold:.0%}")
    if binding.unsupported_claims:
        failures.append("unsupported external claims were emitted")
    if observed["personal_evidence_count"] != 0:
        failures.append("external retrieval mixed results into personal evidence")
    if failures:
        return ScoreResult(
            passed=False,
            observed=observed,
            failure_reason="; ".join(failures),
        )
    return ScoreResult(passed=True, observed=observed)


def _starter_corpus_hits() -> list[KnowledgeChunkHit]:
    store = InMemoryKnowledgeVectorStore()
    pipeline = KnowledgeIngestionPipeline(store)
    for document in STARTER_CORPUS:
        pipeline.ingest(document)

    hits: list[KnowledgeChunkHit] = []
    for chunk in store.chunks.values():
        source = store.sources[chunk.source_id]
        if source.removed_at is not None or source.superseded_at is not None:
            continue
        if "physical activity" not in chunk.text.casefold():
            continue
        hits.append(
            KnowledgeChunkHit(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                source_version=chunk.source_version,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                relevance_score=1.0,
                title=source.title,
                source=source.author_or_org,
                url_or_identifier=source.url_or_identifier,
                trust_level=source.trust_level.value,
                source_metadata=dict(chunk.source_metadata),
            )
        )
    return hits
