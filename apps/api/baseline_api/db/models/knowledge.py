"""Curated external knowledge corpus.

Data classification: Internal (curated, non-personal reference material).
"""

import datetime as dt

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel


class KnowledgeSource(BaseDBModel, table=True):
    """An external reference such as a paper, book, or guideline."""

    __tablename__ = "knowledge_source"
    __table_args__ = (Index("ix_knowledge_source_trust_level", "trust_level"),)

    title: str = Field(nullable=False)
    author_or_org: str | None = Field(default=None)
    source_type: KnowledgeSourceType = Field(
        sa_column=Column(
            SAEnum(KnowledgeSourceType, native_enum=True),
            nullable=False,
        ),
    )
    url_or_identifier: str | None = Field(default=None)
    license_status: str | None = Field(default=None)
    published_at: dt.date | None = Field(default=None)
    ingested_at: dt.datetime = Field(nullable=False)
    version: str = Field(nullable=False)
    trust_level: TrustLevel = Field(
        sa_column=Column(
            SAEnum(TrustLevel, native_enum=True),
            nullable=False,
        ),
    )
