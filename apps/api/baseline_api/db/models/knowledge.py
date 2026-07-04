"""Curated external knowledge corpus.

Data classification: Internal (curated, non-personal reference material).
"""

import datetime as dt
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import validates
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel

KNOWLEDGE_EMBEDDING_DIMENSION = 16


def normalize_embedding(embedding: Sequence[float]) -> list[float]:
    """Return an embedding only if it matches the persisted corpus vector shape."""

    if isinstance(embedding, (bytes, str)) or len(embedding) != KNOWLEDGE_EMBEDDING_DIMENSION:
        raise ValueError(
            f"Knowledge chunk embeddings must have {KNOWLEDGE_EMBEDDING_DIMENSION} dimensions"
        )
    return [float(value) for value in embedding]


class KnowledgeSource(BaseDBModel, table=True):
    """An external reference such as a paper, book, or guideline."""

    __tablename__ = "knowledge_source"
    __table_args__ = (
        Index("ix_knowledge_source_trust_level", "trust_level"),
        Index(
            "ix_knowledge_source_active_identifier",
            "url_or_identifier",
            "superseded_at",
            "removed_at",
        ),
    )

    title: str = Field(nullable=False)
    author_or_org: str = Field(nullable=False)
    source_type: KnowledgeSourceType = Field(
        sa_column=Column(
            SAEnum(KnowledgeSourceType, native_enum=True),
            nullable=False,
        ),
    )
    url_or_identifier: str = Field(nullable=False)
    license_status: str = Field(nullable=False)
    published_at: dt.date = Field(nullable=False)
    ingested_at: dt.datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    version: str = Field(nullable=False)
    trust_level: TrustLevel = Field(
        sa_column=Column(
            SAEnum(TrustLevel, native_enum=True),
            nullable=False,
        ),
    )
    superseded_at: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    removed_at: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class KnowledgeChunk(BaseDBModel, table=True):
    """Embedded chunk from a curated external source.

    Data classification: Internal. Chunks must contain external reference text only.
    """

    __tablename__ = "knowledge_chunk"
    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index", name="uq_knowledge_chunk_source_index"),
        CheckConstraint(
            "jsonb_typeof(embedding) = 'array' "
            f"AND jsonb_array_length(embedding) = {KNOWLEDGE_EMBEDDING_DIMENSION}",
            name="ck_knowledge_chunk_embedding_dimension",
        ),
        Index("ix_knowledge_chunk_source_id", "source_id"),
        Index("ix_knowledge_chunk_content_hash", "content_hash"),
    )

    source_id: UUID = Field(foreign_key="knowledge_source.id", nullable=False)
    source_version: str = Field(nullable=False)
    chunk_index: int = Field(nullable=False)
    text: str = Field(sa_column=Column(Text, nullable=False))
    content_hash: str = Field(nullable=False)
    embedding: list[float] = Field(sa_type=JSONB, default_factory=list)
    source_metadata: dict[str, Any] = Field(sa_type=JSONB, default_factory=dict)

    @validates("embedding")
    def _validate_embedding(self, _key: str, value: list[float]) -> list[float]:
        return normalize_embedding(value)
