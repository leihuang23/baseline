"""Traceable external LLM / model executions.

Data classification: Confidential (model metadata and hashes; no raw prompt payload).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel
from baseline_api.db.models.enums import RunType


class ModelRun(BaseDBModel, table=True):
    """A single model invocation with cost, latency, schema, and safety metadata."""

    __tablename__ = "model_run"
    __table_args__ = (Index("ix_model_run_user_id_created_at", "user_id", "created_at"),)

    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    run_type: RunType = Field(
        sa_column=Column(
            SAEnum(RunType, native_enum=True),
            nullable=False,
        ),
    )
    model_provider: str = Field(nullable=False)
    model_name: str = Field(nullable=False)
    prompt_version: str = Field(nullable=False)
    input_hash: str = Field(nullable=False)
    output_hash: str = Field(nullable=False)
    schema_version: str = Field(nullable=False)
    token_usage: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    cost: float | None = Field(default=None)
    latency_ms: int | None = Field(default=None)
    safety_result: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    input_metadata: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
