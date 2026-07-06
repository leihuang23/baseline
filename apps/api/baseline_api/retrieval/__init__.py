"""Retrieval services for curated non-personal knowledge."""

from baseline_api.retrieval.knowledge import (
    GENERAL_RESEARCH_LABEL,
    CitationBinding,
    KnowledgeChunkHit,
    KnowledgeRetrievalResult,
    KnowledgeRetrievalService,
    bind_external_claims,
    build_external_knowledge_query,
    create_embedder,
    has_external_knowledge_consent,
)

__all__ = [
    "CitationBinding",
    "GENERAL_RESEARCH_LABEL",
    "KnowledgeChunkHit",
    "KnowledgeRetrievalResult",
    "KnowledgeRetrievalService",
    "bind_external_claims",
    "build_external_knowledge_query",
    "create_embedder",
    "has_external_knowledge_consent",
]
