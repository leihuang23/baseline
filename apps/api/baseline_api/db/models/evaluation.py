"""Evaluation harness cases.

Data classification: Confidential when derived from real data; Internal when synthetic.
All cases in the automated test suite should be synthetic by default.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from baseline_api.db.models._base import BaseDBModel


class EvaluationCase(BaseDBModel, table=True):
    """A single evaluated scenario with expected properties and actual output."""

    __tablename__ = "evaluation_case"
    __table_args__ = (Index("ix_evaluation_case_evaluated_at", "evaluated_at"),)

    scenario_name: str = Field(nullable=False)
    input_fixture: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    expected_properties: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    actual_output: dict[str, Any] = Field(
        sa_type=JSONB,
        default_factory=dict,
    )
    pass_fail: bool | None = Field(default=None)
    failure_reason: str | None = Field(default=None)
    evaluated_at: datetime = Field(nullable=False)
