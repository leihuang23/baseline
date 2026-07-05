"""P5-04 cost aggregation and alert tests."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.db.models import (
    AuditEvent,
    BackfillJob,
    DailyAnalysisJob,
    DailyCheckIn,
    ModelRun,
    ReasoningTrace,
    User,
)
from baseline_api.db.models.enums import (
    AuditEventType,
    PrivacyMode,
    RedactionStatus,
    RunType,
    SensitiveNotePolicy,
)
from baseline_api.db.session import get_db_session
from baseline_api.observability.alerts import AlertThresholds, evaluate_operational_alerts
from baseline_api.observability.cost import aggregate_model_run_costs
from baseline_api.privacy.delete import DataDeletionService

TARGET_DATE = dt.date(2026, 7, 4)


def test_cost_latency_aggregation_groups_by_run_model_and_feature(
    db_session: Session,
) -> None:
    user = _seed_user(db_session)
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="primary",
        model="cheap",
        feature="daily_briefing",
        cost=0.10,
        latency_ms=100,
    )
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="primary",
        model="cheap",
        feature="daily_briefing",
        cost=0.25,
        latency_ms=300,
    )
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="fallback",
        model="local",
        feature="follow_up",
        cost=None,
        latency_ms=None,
    )
    db_session.flush()

    report = aggregate_model_run_costs(db_session, user_id=user.id)

    assert report.run_count == 3
    assert report.total_cost == pytest.approx(0.35)
    assert report.total_latency_ms == 400
    assert report.by_run_type["daily_briefing"].run_count == 3
    assert report.by_model["primary/cheap"].run_count == 2
    assert report.by_model["primary/cheap"].average_latency_ms == 200
    assert report.by_feature["daily_briefing"].total_cost == pytest.approx(0.35)
    assert report.by_feature["follow_up"].total_latency_ms == 0


def test_operational_alerts_cover_budget_provider_schema_job_sync_and_deletion(
    db_session: Session,
) -> None:
    user = _seed_user(db_session)
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="primary",
        model="cheap",
        feature="daily_briefing",
        cost=0.15,
        latency_ms=100,
        safety_result={"status": "provider_error"},
    )
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="primary",
        model="cheap",
        feature="daily_briefing",
        cost=0.15,
        latency_ms=120,
        safety_result={"status": "provider_error"},
    )
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="primary",
        model="cheap",
        feature="daily_briefing",
        cost=0.05,
        latency_ms=80,
        safety_result={"status": "schema_invalid"},
    )
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="fallback",
        model="local",
        feature="daily_briefing",
        cost=0.05,
        latency_ms=90,
        safety_result={"status": "schema_invalid"},
    )
    db_session.add(
        DailyAnalysisJob(
            user_id=user.id,
            date=TARGET_DATE,
            status="failed",
            force_recompute=False,
            include_external_knowledge=False,
            privacy_mode=PrivacyMode.local_only.value,
            request_trace_id="trace-id",
            error_code="RuntimeError",
            error_message="Daily briefing generation failed.",
        )
    )
    db_session.add(
        BackfillJob(
            user_id=user.id,
            source_platform="apple_health",
            source_device="watch",
            timezone="UTC",
            start_date=TARGET_DATE,
            end_date=TARGET_DATE,
            chunk_days=1,
            next_start_date=TARGET_DATE,
            status="failed",
            last_error="sync unavailable",
        )
    )
    db_session.add(
        AuditEvent(
            user_id=user.id,
            event_type=AuditEventType.data_deleted,
            actor="system",
            timestamp=dt.datetime(2026, 7, 4, tzinfo=dt.UTC),
            event_metadata={"target": "all", "status": "failed", "error_code": "storage"},
            redaction_status=RedactionStatus.redacted,
        )
    )
    db_session.flush()

    alerts = evaluate_operational_alerts(
        db_session,
        thresholds=AlertThresholds(
            daily_briefing_cost_budget=0.20,
            model_provider_failure_threshold=2,
            schema_validation_failure_threshold=2,
            daily_briefing_failure_threshold=1,
            sync_failure_threshold=1,
            deletion_failure_threshold=1,
        ),
    )

    by_type = {alert.alert_type: alert for alert in alerts}
    assert set(by_type) == {
        "cost_budget_exceeded",
        "model_provider_failures",
        "schema_validation_failures",
        "daily_briefing_generation_failures",
        "sync_failures",
        "deletion_failures",
    }
    assert by_type["cost_budget_exceeded"].metadata["actual_cost"] == pytest.approx(0.40)
    assert by_type["model_provider_failures"].metadata == {"provider": "primary", "count": 2}
    for alert in alerts:
        assert Path(alert.runbook).exists()


def test_default_cost_budget_alert_uses_current_utc_day_window(db_session: Session) -> None:
    user = _seed_user(db_session)
    old_created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="primary",
        model="old",
        feature="daily_briefing",
        cost=5.00,
        latency_ms=100,
        created_at=old_created_at,
    )
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="primary",
        model="today",
        feature="daily_briefing",
        cost=0.05,
        latency_ms=100,
    )
    db_session.flush()

    alerts = evaluate_operational_alerts(
        db_session,
        thresholds=AlertThresholds(
            daily_briefing_cost_budget=1.00,
            model_provider_failure_threshold=10,
            schema_validation_failure_threshold=10,
            daily_briefing_failure_threshold=10,
            sync_failure_threshold=10,
            deletion_failure_threshold=10,
        ),
    )

    assert "cost_budget_exceeded" not in {alert.alert_type for alert in alerts}


def test_observability_alert_endpoint_enforces_settings_thresholds(db_session: Session) -> None:
    user = _seed_user(db_session)
    _add_model_run(
        db_session,
        user_id=user.id,
        provider="primary",
        model="cheap",
        feature="daily_briefing",
        cost=0.25,
        latency_ms=100,
    )
    db_session.flush()
    app = create_app(
        Settings(
            APP_ENV="test",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            DAILY_BRIEFING_COST_BUDGET=0.20,
            MODEL_PROVIDER_FAILURE_ALERT_THRESHOLD=10,
            SCHEMA_VALIDATION_FAILURE_ALERT_THRESHOLD=10,
            DAILY_BRIEFING_FAILURE_ALERT_THRESHOLD=10,
            SYNC_FAILURE_ALERT_THRESHOLD=10,
            DELETION_FAILURE_ALERT_THRESHOLD=10,
        )
    )

    def override_session() -> Session:
        return db_session

    app.dependency_overrides[get_db_session] = override_session
    response = TestClient(app).get("/v1/observability/alerts")

    assert response.status_code == 200
    alerts = response.json()["data"]
    assert [alert["alert_type"] for alert in alerts] == ["cost_budget_exceeded"]
    assert alerts[0]["metadata"]["budget"] == 0.20
    assert alerts[0]["metadata"]["actual_cost"] == pytest.approx(0.25)


def test_sync_alert_counts_degraded_briefing_freshness_trace(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(
        ReasoningTrace(
            user_id=user.id,
            date=TARGET_DATE,
            trace_version="p5-04-test",
            assessment_version="p5-04-test",
            input_hash="sync-degraded",
            trace_payload={
                "briefing_generation": {
                    "degraded_stages": [{"stage": "sync", "reason": "RuntimeError"}],
                    "stages": [],
                }
            },
        )
    )
    db_session.flush()

    alerts = evaluate_operational_alerts(
        db_session,
        thresholds=AlertThresholds(
            daily_briefing_cost_budget=10.00,
            model_provider_failure_threshold=10,
            schema_validation_failure_threshold=10,
            daily_briefing_failure_threshold=10,
            sync_failure_threshold=1,
            deletion_failure_threshold=10,
        ),
    )

    by_type = {alert.alert_type: alert for alert in alerts}
    assert by_type["sync_failures"].metadata["count"] == 1
    assert by_type["sync_failures"].metadata["briefing_sync_degradations"] == 1


def test_real_deletion_exception_emits_failed_audit_and_alerts(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _seed_user(db_session)
    checkin = DailyCheckIn(
        user_id=user.id,
        date=TARGET_DATE,
        sensitive_note_policy=SensitiveNotePolicy.exclude_from_external_llm,
    )
    db_session.add(checkin)
    db_session.flush()
    checkin_id = checkin.id
    db_session.commit()

    def fail_derived_delete(self: DataDeletionService, **_: object) -> dict[str, int]:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(
        DataDeletionService,
        "_delete_checkin_derived_artifacts",
        fail_derived_delete,
    )

    with pytest.raises(RuntimeError, match="storage unavailable"):
        DataDeletionService(db_session).delete_checkin(checkin_id)

    audit = db_session.exec(select(AuditEvent)).one()
    assert audit.event_type == AuditEventType.data_deleted
    assert audit.event_metadata["target"] == "checkin"
    assert audit.event_metadata["status"] == "failed"
    assert audit.event_metadata["error_code"] == "RuntimeError"

    alerts = evaluate_operational_alerts(
        db_session,
        thresholds=AlertThresholds(
            daily_briefing_cost_budget=10.00,
            model_provider_failure_threshold=10,
            schema_validation_failure_threshold=10,
            daily_briefing_failure_threshold=10,
            sync_failure_threshold=10,
            deletion_failure_threshold=1,
        ),
    )

    assert "deletion_failures" in {alert.alert_type for alert in alerts}


def _seed_user(session: Session) -> User:
    user = User(
        privacy_mode=PrivacyMode.cloud_assisted,
        active_consent_version="v1",
    )
    session.add(user)
    session.flush()
    return user


def _add_model_run(
    session: Session,
    *,
    user_id: UUID,
    provider: str,
    model: str,
    feature: str,
    cost: float | None,
    latency_ms: int | None,
    safety_result: dict[str, object] | None = None,
    created_at: dt.datetime | None = None,
) -> None:
    timestamp = created_at or dt.datetime.now(dt.UTC)
    session.add(
        ModelRun(
            user_id=user_id,
            created_at=timestamp,
            updated_at=timestamp,
            run_type=RunType.daily_briefing,
            model_provider=provider,
            model_name=model,
            prompt_version="test-v1",
            input_hash=f"input-{provider}-{model}-{feature}-{latency_ms}",
            output_hash=f"output-{provider}-{model}-{feature}-{latency_ms}",
            schema_version="llm_explanation_v1",
            token_usage={"total": 10},
            cost=cost,
            latency_ms=latency_ms,
            safety_result=safety_result or {"status": "passed"},
            input_metadata={"feature": feature},
        )
    )
