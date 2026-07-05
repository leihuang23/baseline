"""Deterministic operational alert evaluators.

Alerts are returned as structured records for dashboard or paging adapters. They
contain only counts, model metadata, and runbook references.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, col, select

from baseline_api.config import Settings
from baseline_api.db.models.assessment import DailyAnalysisJob, ReasoningTrace
from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.models.enums import AuditEventType
from baseline_api.db.models.ingestion import BackfillJob, HealthImportBatch
from baseline_api.db.models.modelrun import ModelRun
from baseline_api.observability.cost import CostLatencyReport, aggregate_model_run_costs


@dataclass(frozen=True, slots=True)
class AlertThresholds:
    """Configurable budgets and failure thresholds for operator alerts."""

    daily_briefing_cost_budget: float
    model_provider_failure_threshold: int
    schema_validation_failure_threshold: int
    daily_briefing_failure_threshold: int
    sync_failure_threshold: int
    deletion_failure_threshold: int

    @classmethod
    def from_settings(cls, settings: Settings) -> AlertThresholds:
        return cls(
            daily_briefing_cost_budget=settings.daily_briefing_cost_budget,
            model_provider_failure_threshold=settings.model_provider_failure_alert_threshold,
            schema_validation_failure_threshold=settings.schema_validation_failure_alert_threshold,
            daily_briefing_failure_threshold=settings.daily_briefing_failure_alert_threshold,
            sync_failure_threshold=settings.sync_failure_alert_threshold,
            deletion_failure_threshold=settings.deletion_failure_alert_threshold,
        )


@dataclass(frozen=True, slots=True)
class OperationalAlert:
    """A privacy-safe alert record for operator surfaces."""

    alert_type: str
    severity: str
    message: str
    runbook: str
    metadata: dict[str, Any] = field(default_factory=dict)


def evaluate_operational_alerts(
    session: Session,
    *,
    thresholds: AlertThresholds,
    since: dt.datetime | None = None,
) -> list[OperationalAlert]:
    """Evaluate all P5-04 alert families for recent operational state."""

    cost_report = aggregate_model_run_costs(session, start_at=since or _current_utc_day_start())
    alerts: list[OperationalAlert] = []
    alerts.extend(cost_budget_alerts(cost_report, thresholds=thresholds))
    alerts.extend(model_provider_failure_alerts(session, thresholds=thresholds, since=since))
    alerts.extend(schema_validation_alerts(session, thresholds=thresholds, since=since))
    alerts.extend(daily_briefing_failure_alerts(session, thresholds=thresholds, since=since))
    alerts.extend(sync_failure_alerts(session, thresholds=thresholds, since=since))
    alerts.extend(deletion_failure_alerts(session, thresholds=thresholds, since=since))
    return alerts


def evaluate_configured_operational_alerts(
    session: Session,
    *,
    settings: Settings,
    since: dt.datetime | None = None,
) -> list[OperationalAlert]:
    """Evaluate alerts using runtime settings-backed budgets and thresholds."""

    return evaluate_operational_alerts(
        session,
        thresholds=AlertThresholds.from_settings(settings),
        since=since,
    )


def cost_budget_alerts(
    report: CostLatencyReport,
    *,
    thresholds: AlertThresholds,
) -> list[OperationalAlert]:
    """Alert when daily briefing cost exceeds the configured budget."""

    daily = report.by_feature.get("daily_briefing") or report.by_run_type.get("daily_briefing")
    total_cost = daily.total_cost if daily is not None else 0.0
    if total_cost <= thresholds.daily_briefing_cost_budget:
        return []
    return [
        OperationalAlert(
            alert_type="cost_budget_exceeded",
            severity="warning",
            message="Daily briefing model cost exceeded the configured budget.",
            runbook="docs/runbooks/cost-budget-exceeded.md",
            metadata={
                "budget": thresholds.daily_briefing_cost_budget,
                "actual_cost": round(total_cost, 6),
                "run_count": daily.run_count if daily is not None else 0,
            },
        )
    ]


def model_provider_failure_alerts(
    session: Session,
    *,
    thresholds: AlertThresholds,
    since: dt.datetime | None = None,
) -> list[OperationalAlert]:
    rows = _model_runs(session, since=since)
    counts: dict[str, int] = {}
    for row in rows:
        if _safety_status(row) != "provider_error":
            continue
        counts[row.model_provider] = counts.get(row.model_provider, 0) + 1
    return [
        OperationalAlert(
            alert_type="model_provider_failures",
            severity="critical",
            message="Model provider failures exceeded the configured threshold.",
            runbook="docs/runbooks/model-provider-failures.md",
            metadata={"provider": provider, "count": count},
        )
        for provider, count in sorted(counts.items())
        if count >= thresholds.model_provider_failure_threshold
    ]


def schema_validation_alerts(
    session: Session,
    *,
    thresholds: AlertThresholds,
    since: dt.datetime | None = None,
) -> list[OperationalAlert]:
    count = sum(
        1 for row in _model_runs(session, since=since) if _safety_status(row) == "schema_invalid"
    )
    if count < thresholds.schema_validation_failure_threshold:
        return []
    return [
        OperationalAlert(
            alert_type="schema_validation_failures",
            severity="critical",
            message="Schema validation failures exceeded the configured threshold.",
            runbook="docs/runbooks/schema-validation-failures.md",
            metadata={"count": count},
        )
    ]


def daily_briefing_failure_alerts(
    session: Session,
    *,
    thresholds: AlertThresholds,
    since: dt.datetime | None = None,
) -> list[OperationalAlert]:
    statement = select(DailyAnalysisJob).where(DailyAnalysisJob.status == "failed")
    if since is not None:
        statement = statement.where(DailyAnalysisJob.created_at >= since)
    rows = session.exec(statement).all()
    if len(rows) < thresholds.daily_briefing_failure_threshold:
        return []
    return [
        OperationalAlert(
            alert_type="daily_briefing_generation_failures",
            severity="critical",
            message="Daily briefing generation failures exceeded the configured threshold.",
            runbook="docs/runbooks/daily-briefing-generation-failures.md",
            metadata={"count": len(rows)},
        )
    ]


def sync_failure_alerts(
    session: Session,
    *,
    thresholds: AlertThresholds,
    since: dt.datetime | None = None,
) -> list[OperationalAlert]:
    backfill_statement = select(BackfillJob)
    batch_statement = select(HealthImportBatch)
    if since is not None:
        backfill_statement = backfill_statement.where(BackfillJob.created_at >= since)
        batch_statement = batch_statement.where(HealthImportBatch.created_at >= since)
    failed_backfills = [
        row
        for row in session.exec(backfill_statement).all()
        if row.status in {"failed", "error"} or row.last_error
    ]
    failed_batches = [
        row
        for row in session.exec(batch_statement).all()
        if _sync_batch_failed(row.data_quality_summary)
    ]
    degraded_trace_count = _sync_degraded_trace_count(session, since=since)
    count = len(failed_backfills) + len(failed_batches) + degraded_trace_count
    if count < thresholds.sync_failure_threshold:
        return []
    return [
        OperationalAlert(
            alert_type="sync_failures",
            severity="critical",
            message="Sync failures exceeded the configured threshold.",
            runbook="docs/runbooks/sync-failures.md",
            metadata={
                "count": count,
                "backfill_failures": len(failed_backfills),
                "batch_failures": len(failed_batches),
                "briefing_sync_degradations": degraded_trace_count,
            },
        )
    ]


def deletion_failure_alerts(
    session: Session,
    *,
    thresholds: AlertThresholds,
    since: dt.datetime | None = None,
) -> list[OperationalAlert]:
    statement = select(AuditEvent).where(
        col(AuditEvent.event_type).in_([AuditEventType.data_deleted, AuditEventType.memory_deleted])
    )
    if since is not None:
        statement = statement.where(AuditEvent.created_at >= since)
    failed = [
        row for row in session.exec(statement).all() if _deletion_event_failed(row.event_metadata)
    ]
    if len(failed) < thresholds.deletion_failure_threshold:
        return []
    return [
        OperationalAlert(
            alert_type="deletion_failures",
            severity="critical",
            message="Deletion failures exceeded the configured threshold.",
            runbook="docs/runbooks/deletion-failures.md",
            metadata={"count": len(failed)},
        )
    ]


def _model_runs(session: Session, *, since: dt.datetime | None) -> list[ModelRun]:
    statement = select(ModelRun)
    if since is not None:
        statement = statement.where(ModelRun.created_at >= since)
    return list(session.exec(statement.order_by(col(ModelRun.created_at))).all())


def _safety_status(row: ModelRun) -> str:
    result = row.safety_result if isinstance(row.safety_result, dict) else {}
    status = result.get("status")
    return status if isinstance(status, str) else "unknown"


def _sync_batch_failed(summary: Any) -> bool:
    if not isinstance(summary, dict):
        return False
    status = summary.get("status")
    return status in {"failed", "error"}


def _deletion_event_failed(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    status = metadata.get("status")
    return status in {"failed", "error"} or "error_code" in metadata


def _sync_degraded_trace_count(session: Session, *, since: dt.datetime | None) -> int:
    statement = select(ReasoningTrace)
    if since is not None:
        statement = statement.where(ReasoningTrace.created_at >= since)
    return sum(1 for row in session.exec(statement).all() if _trace_has_sync_degradation(row))


def _trace_has_sync_degradation(trace: ReasoningTrace) -> bool:
    payload = trace.trace_payload if isinstance(trace.trace_payload, dict) else {}
    generation = payload.get("briefing_generation")
    if not isinstance(generation, dict):
        return False
    degraded_stages = generation.get("degraded_stages")
    if isinstance(degraded_stages, list) and any(
        _stage_name(stage) == "sync" for stage in degraded_stages
    ):
        return True
    stages = generation.get("stages")
    return isinstance(stages, list) and any(
        _stage_name(stage) == "data_freshness" and _stage_status(stage) == "degraded"
        for stage in stages
    )


def _stage_name(stage: Any) -> str | None:
    if not isinstance(stage, dict):
        return None
    value = stage.get("stage")
    return value if isinstance(value, str) else None


def _stage_status(stage: Any) -> str | None:
    if not isinstance(stage, dict):
        return None
    value = stage.get("status")
    return value if isinstance(value, str) else None


def _current_utc_day_start() -> dt.datetime:
    now = dt.datetime.now(dt.UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
