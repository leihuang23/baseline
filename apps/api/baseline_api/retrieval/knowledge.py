"""External knowledge retrieval and citation binding.

The retrieval boundary reads only the curated external corpus. Personal health data stays
in SQL/time-series retrieval paths owned by the assistant and briefing services.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from packages.knowledge.embeddings import EmbeddingProvider, HashEmbeddingProvider
from sqlmodel import Session, col, select

from baseline_api.db.models.knowledge import KnowledgeChunk, KnowledgeSource
from baseline_api.db.models.user import ConsentRecord
from baseline_api.schemas.recommendation import ExternalCitation

DEFAULT_LIMIT = 3
DEFAULT_MIN_RELEVANCE = 0.08
LEXICAL_FALLBACK_MIN_OVERLAP = 0.2
MIN_CLAIM_SUPPORT_SCORE = 0.35
GENERAL_RESEARCH_LABEL = "General research (non-personalized): "

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


@dataclass(frozen=True, slots=True)
class KnowledgeChunkHit:
    """A relevant external-corpus chunk with source metadata."""

    chunk_id: UUID
    source_id: UUID
    source_version: str
    chunk_index: int
    text: str
    relevance_score: float
    title: str
    source: str
    url_or_identifier: str
    trust_level: str
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def cited_claim(self) -> str:
        return GENERAL_RESEARCH_LABEL + _claim_from_text(self.text)

    def to_prompt_dict(self) -> dict[str, Any]:
        """Return a minimized object safe for LLM prompt construction."""

        return {
            "chunk_id": str(self.chunk_id),
            "source_id": str(self.source_id),
            "source_version": self.source_version,
            "chunk_index": self.chunk_index,
            "title": self.title,
            "source": self.source,
            "url_or_identifier": self.url_or_identifier,
            "trust_level": self.trust_level,
            "relevance_score": self.relevance_score,
            "cited_claim": self.cited_claim,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class CitationBinding:
    """Supported citations plus unsupported claims suppressed from user output."""

    citations: list[ExternalCitation]
    supported_claims: list[str]
    unsupported_claims: list[str]
    citation_accuracy: float


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalResult:
    """External retrieval result kept separate from personal evidence."""

    hits: list[KnowledgeChunkHit]
    citations: list[ExternalCitation]
    external_knowledge: list[dict[str, Any]]
    uncertainty: list[str]
    degraded: bool = False
    degrade_reason: str | None = None
    citation_accuracy: float = 1.0


class KnowledgeRetrievalService:
    """Retrieve relevant active chunks from the curated external corpus."""

    def __init__(
        self,
        session: Session,
        *,
        embedder: EmbeddingProvider | None = None,
        min_relevance: float = DEFAULT_MIN_RELEVANCE,
    ) -> None:
        self._session = session
        self._embedder = embedder or HashEmbeddingProvider()
        self._min_relevance = min_relevance

    def retrieve(self, query: str, *, limit: int = DEFAULT_LIMIT) -> KnowledgeRetrievalResult:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            return _empty_result("External retrieval skipped because the query was empty.")
        try:
            query_embedding = self._embedder.embed(normalized_query)
            hits = self._rank_hits(normalized_query, query_embedding, limit=limit)
        except Exception as exc:
            try:
                hits = self._rank_lexical_hits(normalized_query, limit=limit)
            except Exception:
                hits = []
            if hits:
                return _result_from_hits(
                    hits,
                    uncertainty=[
                        (
                            "External vector retrieval was unavailable; lexical corpus "
                            "retrieval was used."
                        ),
                        ("External sources are general research context, not personalized advice."),
                    ],
                )
            return KnowledgeRetrievalResult(
                hits=[],
                citations=[],
                external_knowledge=[],
                uncertainty=[
                    "External knowledge retrieval was unavailable; no external claims were used."
                ],
                degraded=True,
                degrade_reason=type(exc).__name__,
            )
        if not hits:
            hits = self._rank_lexical_hits(normalized_query, limit=limit)
        if not hits:
            return _empty_result("No relevant curated external source met the retrieval threshold.")

        return _result_from_hits(
            hits,
            uncertainty=[
                ("External sources are general research context, not personalized advice.")
            ],
        )

    def _rank_hits(
        self,
        query: str,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[KnowledgeChunkHit]:
        chunks = self._session.exec(
            select(KnowledgeChunk).order_by(col(KnowledgeChunk.chunk_index))
        ).all()
        hits: list[KnowledgeChunkHit] = []
        for chunk in chunks:
            source = self._session.get(KnowledgeSource, chunk.source_id)
            if source is None or source.superseded_at is not None or source.removed_at is not None:
                continue
            trust_level = _trust_level_value(source.trust_level)
            if trust_level is None:
                continue
            try:
                source_metadata = _metadata(chunk.source_metadata)
                score = _relevance_score(query, query_embedding, chunk.text, chunk.embedding)
            except (TypeError, ValueError):
                continue
            if score < self._min_relevance:
                continue
            hits.append(
                KnowledgeChunkHit(
                    chunk_id=chunk.id,
                    source_id=chunk.source_id,
                    source_version=chunk.source_version,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    relevance_score=score,
                    title=source.title,
                    source=source.author_or_org,
                    url_or_identifier=source.url_or_identifier,
                    trust_level=trust_level,
                    source_metadata=source_metadata,
                )
            )
        return sorted(hits, key=lambda hit: hit.relevance_score, reverse=True)[:limit]

    def _rank_lexical_hits(self, query: str, *, limit: int) -> list[KnowledgeChunkHit]:
        chunks = self._session.exec(
            select(KnowledgeChunk).order_by(col(KnowledgeChunk.chunk_index))
        ).all()
        hits: list[KnowledgeChunkHit] = []
        for chunk in chunks:
            source = self._session.get(KnowledgeSource, chunk.source_id)
            if source is None or source.superseded_at is not None or source.removed_at is not None:
                continue
            trust_level = _trust_level_value(source.trust_level)
            if trust_level is None:
                continue
            try:
                source_metadata = _metadata(chunk.source_metadata)
            except (TypeError, ValueError):
                continue
            score = _token_overlap(query, chunk.text)
            if score < LEXICAL_FALLBACK_MIN_OVERLAP:
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
                    trust_level=trust_level,
                    source_metadata=source_metadata,
                )
            )
        return sorted(hits, key=lambda hit: hit.relevance_score, reverse=True)[:limit]


def bind_external_claims(
    claims: Sequence[str],
    hits: Sequence[KnowledgeChunkHit],
    *,
    min_support_score: float = MIN_CLAIM_SUPPORT_SCORE,
) -> CitationBinding:
    """Bind supported external claims to corpus sources and suppress unsupported claims."""

    citations: list[ExternalCitation] = []
    supported_claims: list[str] = []
    unsupported_claims: list[str] = []
    seen_claims: set[str] = set()
    for claim in claims:
        normalized_claim = " ".join(claim.split())
        if not normalized_claim or normalized_claim in seen_claims:
            continue
        seen_claims.add(normalized_claim)
        hit = _best_supporting_hit(normalized_claim, hits, min_support_score)
        if hit is None:
            unsupported_claims.append(normalized_claim)
            continue
        citations.append(_citation_for_hit(hit, normalized_claim))
        supported_claims.append(normalized_claim)

    total_claims = len(supported_claims) + len(unsupported_claims)
    citation_accuracy = 1.0 if total_claims == 0 else len(supported_claims) / total_claims
    return CitationBinding(
        citations=citations,
        supported_claims=supported_claims,
        unsupported_claims=unsupported_claims,
        citation_accuracy=round(citation_accuracy, 4),
    )


def has_external_knowledge_consent(session: Session, user_id: UUID) -> bool:
    """Return whether the active user consent permits opt-in external knowledge use."""

    record = session.exec(
        select(ConsentRecord)
        .where(
            ConsentRecord.user_id == user_id,
            col(ConsentRecord.revoked_at).is_(None),
        )
        .order_by(col(ConsentRecord.timestamp).desc())
    ).first()
    return bool(
        record is not None and record.cloud_processing_enabled and record.external_llm_enabled
    )


def _empty_result(uncertainty: str) -> KnowledgeRetrievalResult:
    return KnowledgeRetrievalResult(
        hits=[],
        citations=[],
        external_knowledge=[],
        uncertainty=[uncertainty],
    )


def _result_from_hits(
    hits: Sequence[KnowledgeChunkHit],
    *,
    uncertainty: list[str],
) -> KnowledgeRetrievalResult:
    binding = bind_external_claims([hit.cited_claim for hit in hits], hits)
    return KnowledgeRetrievalResult(
        hits=list(hits),
        citations=binding.citations,
        external_knowledge=[hit.to_prompt_dict() for hit in hits],
        uncertainty=uncertainty,
        citation_accuracy=binding.citation_accuracy,
    )


def _metadata(raw_metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw_metadata is None:
        return {}
    if not isinstance(raw_metadata, Mapping):
        raise TypeError("Knowledge chunk source_metadata must be a mapping")
    return {str(key): value for key, value in raw_metadata.items()}


def _trust_level_value(value: Any) -> str | None:
    if hasattr(value, "value"):
        return str(value.value)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _relevance_score(
    query: str,
    query_embedding: Sequence[float],
    text: str,
    chunk_embedding: Sequence[float],
) -> float:
    vector_score = max(0.0, _cosine_similarity(query_embedding, chunk_embedding))
    lexical_score = _token_overlap(query, text)
    if lexical_score == 0.0:
        return 0.0
    return round((vector_score * 0.6) + (lexical_score * 0.4), 6)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
    ) / (left_norm * right_norm)


def _token_overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    if not left_tokens:
        return 0.0
    right_tokens = _tokens(right)
    if not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in TOKEN_PATTERN.findall(value.casefold())
        if token not in STOPWORDS and len(token) > 2
    }


def _claim_from_text(text: str) -> str:
    normalized = " ".join(text.split())
    first_sentence = normalized.split(". ", 1)[0].strip()
    if not first_sentence:
        return "Curated external source was retrieved for this topic."
    return first_sentence if first_sentence.endswith(".") else f"{first_sentence}."


def _best_supporting_hit(
    claim: str,
    hits: Sequence[KnowledgeChunkHit],
    min_support_score: float,
) -> KnowledgeChunkHit | None:
    support = [
        (_claim_support_score(claim, hit), hit)
        for hit in hits
        if _claim_support_score(claim, hit) >= min_support_score
    ]
    if not support:
        return None
    return max(support, key=lambda item: item[0])[1]


def _claim_support_score(claim: str, hit: KnowledgeChunkHit) -> float:
    claim_text = claim.removeprefix(GENERAL_RESEARCH_LABEL)
    if claim_text.casefold() in hit.text.casefold():
        return 1.0
    return _token_overlap(claim_text, hit.text)


def _citation_for_hit(hit: KnowledgeChunkHit, claim: str) -> ExternalCitation:
    payload = {
        "title": hit.title,
        "source": f"{hit.source} ({hit.source_version})",
        "url": hit.url_or_identifier if _looks_like_url(hit.url_or_identifier) else None,
        "cited_claim": claim,
    }
    return ExternalCitation.model_validate(payload)


def _looks_like_url(value: str) -> bool:
    normalized = value.strip().casefold()
    return normalized.startswith("http://") or normalized.startswith("https://")
