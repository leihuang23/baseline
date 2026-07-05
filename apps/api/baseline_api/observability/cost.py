"""Cost and latency aggregation over persisted model runs.

Data classification: Internal. Aggregates model metadata only; never reads or emits
raw prompt or output payloads.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, select

from baseline_api.db.models.modelrun import ModelRun


@dataclass(frozen=True, slots=True)
class ModelRunCostRecord:
    """Privacy-safe cost record for one persisted model run."""

    run_id: UUID
    created_at: dt.datetime
    run_type: str
    model_provider: str
    model_name: str
    feature: str
    cost: float
    latency_ms: int


@dataclass(frozen=True, slots=True)
class CostLatencyBucket:
    """Aggregated cost and latency for a dashboard dimension."""

    key: str
    run_count: int
    total_cost: float
    total_latency_ms: int
    average_latency_ms: float


@dataclass(frozen=True, slots=True)
class CostLatencyReport:
    """Cost visibility grouped by run, model, and feature dimensions."""

    runs: list[ModelRunCostRecord]
    total_cost: float
    total_latency_ms: int
    by_run_type: dict[str, CostLatencyBucket] = field(default_factory=dict)
    by_model: dict[str, CostLatencyBucket] = field(default_factory=dict)
    by_feature: dict[str, CostLatencyBucket] = field(default_factory=dict)

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def average_latency_ms(self) -> float:
        if not self.runs:
            return 0.0
        return self.total_latency_ms / len(self.runs)


def aggregate_model_run_costs(
    session: Session,
    *,
    user_id: UUID | None = None,
    start_at: dt.datetime | None = None,
    end_at: dt.datetime | None = None,
) -> CostLatencyReport:
    """Return privacy-safe cost and latency aggregates for persisted model runs."""

    statement = select(ModelRun)
    if user_id is not None:
        statement = statement.where(ModelRun.user_id == user_id)
    if start_at is not None:
        statement = statement.where(ModelRun.created_at >= start_at)
    if end_at is not None:
        statement = statement.where(ModelRun.created_at < end_at)
    statement = statement.order_by(col(ModelRun.created_at))

    records = [_record_from_model_run(row) for row in session.exec(statement).all()]
    return aggregate_cost_records(records)


def aggregate_cost_records(records: list[ModelRunCostRecord]) -> CostLatencyReport:
    """Aggregate an already materialized set of model-run cost records."""

    return CostLatencyReport(
        runs=records,
        total_cost=sum(record.cost for record in records),
        total_latency_ms=sum(record.latency_ms for record in records),
        by_run_type=_bucket(records, key=lambda record: record.run_type),
        by_model=_bucket(
            records,
            key=lambda record: f"{record.model_provider}/{record.model_name}",
        ),
        by_feature=_bucket(records, key=lambda record: record.feature),
    )


def _record_from_model_run(row: ModelRun) -> ModelRunCostRecord:
    return ModelRunCostRecord(
        run_id=row.id,
        created_at=row.created_at,
        run_type=_enum_value(row.run_type),
        model_provider=row.model_provider,
        model_name=row.model_name,
        feature=_feature_name(row),
        cost=_safe_float(row.cost),
        latency_ms=_safe_int(row.latency_ms),
    )


def _bucket(
    records: list[ModelRunCostRecord],
    *,
    key: Any,
) -> dict[str, CostLatencyBucket]:
    totals: dict[str, list[ModelRunCostRecord]] = {}
    for record in records:
        totals.setdefault(str(key(record)), []).append(record)
    return {
        bucket_key: CostLatencyBucket(
            key=bucket_key,
            run_count=len(bucket_records),
            total_cost=sum(record.cost for record in bucket_records),
            total_latency_ms=sum(record.latency_ms for record in bucket_records),
            average_latency_ms=(
                sum(record.latency_ms for record in bucket_records) / len(bucket_records)
            ),
        )
        for bucket_key, bucket_records in totals.items()
    }


def _feature_name(row: ModelRun) -> str:
    metadata = row.input_metadata if isinstance(row.input_metadata, dict) else {}
    feature = metadata.get("feature") or metadata.get("task_type")
    if isinstance(feature, str) and feature:
        return feature
    return _enum_value(row.run_type)


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _safe_float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _safe_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0
