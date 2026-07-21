"""Tests for recommendation feedback and outcome routing."""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Generator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.db.models.assessment import Recommendation
from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.models.enums import (
    AuditEventType,
    PeriodType,
    PrivacyMode,
    RecommendationType,
    SafetyStatus,
)
from baseline_api.db.models.evaluation import EvaluationCase
from baseline_api.db.models.memory import MemorySummary
from baseline_api.db.models.user import User
from baseline_api.db.repositories.evaluation import EvaluationCaseRepository
from baseline_api.db.repositories.memory import MemorySummaryRepository
from baseline_api.db.session import get_db_session
from baseline_api.feedback.service import FEEDBACK_EVAL_SCENARIO
from baseline_api.safety.engine import SafetyPolicyEngine

POLICY_PATH = Path(__file__).resolve().parents[3] / "packages/eval/policy/safety_policy.json"
DEFAULT_REASON_SIGNAL = "REASONING_DISAGREEMENT_SIGNAL"
DEFAULT_OUTCOME_SIGNAL = "OUTCOME_SIGNAL_A"
SAFETY_REASON_SIGNAL = "UNSAFE"
SAFETY_OUTCOME_SIGNAL = "OUTCOME_SIGNAL_B"
CONTRADICTION_REASON_SIGNAL = "READY"
CONTRADICTION_OUTCOME_SIGNAL = "OUTCOME_SIGNAL_C"


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )


def _client(db_session: Session) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def _seed_user(db_session: Session) -> User:
    user = User(
        privacy_mode=PrivacyMode.local_only,
        active_consent_version="v1",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _recommendation(
    db_session: Session,
    user: User,
    *,
    target_date: dt.date = dt.date(2026, 1, 15),
    band: str = "recovery",
    text: str = "RECOMMENDATION_SIGNAL",
) -> Recommendation:
    recommendation = Recommendation(
        user_id=user.id,
        date=target_date,
        recommendation_type=RecommendationType.training,
        recommendation_text=text,
        candidate_options=[],
        evidence_refs=[{"metric": "sleep_debt_hours"}],
        safety_status=SafetyStatus.passed,
        safety_result={"status": "passed", "policy_version": "0.1.0"},
        briefing_payload={
            "recommendation_band": band,
            "trace_id": "11111111-1111-4111-8111-111111111111",
        },
    )
    db_session.add(recommendation)
    db_session.flush()
    return recommendation


def _post_feedback(
    client: TestClient,
    recommendation_id: UUID,
    **overrides: Any,
) -> dict[str, Any]:
    payload = {
        "rating": "useful",
        "action_taken": "followed",
        "reason": DEFAULT_REASON_SIGNAL,
        "outcome_notes": DEFAULT_OUTCOME_SIGNAL,
    }
    payload.update(overrides)
    response = client.post(f"/v1/recommendations/{recommendation_id}/feedback", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def test_feedback_routes_to_memory_eval_and_redacted_audit(db_session: Session) -> None:
    user = _seed_user(db_session)
    recommendation = _recommendation(db_session, user)
    client = _client(db_session)

    data = _post_feedback(client, recommendation.id)

    assert data["memory_update_status"] == "applied"
    assert data["eval_queue_status"] == "queued"
    assert data["contradiction_alert"] is None

    db_session.refresh(recommendation)
    assert recommendation.accepted_action is not None
    assert recommendation.user_feedback is not None
    accepted_action = recommendation.accepted_action
    feedback = recommendation.user_feedback
    assert accepted_action["action_taken"] == "followed"
    assert feedback["feedback_id"] == data["feedback_id"]
    assert feedback["rating"] == "useful"
    assert feedback["memory_update_status"] == "applied"
    assert feedback["eval_queue_status"] == "queued"
    assert feedback["reason"]["present"] is True
    assert feedback["reason"]["redaction_status"] == "redacted"
    assert feedback["outcome_notes"]["present"] is True
    assert feedback["outcome_notes"]["redaction_status"] == "redacted"
    assert feedback["outcome"]["linked_recommendation_id"] == str(recommendation.id)
    assert DEFAULT_REASON_SIGNAL not in str(feedback)
    assert DEFAULT_OUTCOME_SIGNAL not in str(feedback)

    memory_summary = db_session.exec(select(MemorySummary)).one()
    assert memory_summary.period_type is PeriodType.daily
    feedback_observation = memory_summary.observations[0]
    assert feedback_observation["key"] == "recommendation_feedback"
    assert feedback_observation["value"]["feedback_id"] == data["feedback_id"]
    assert feedback_observation["value"]["outcome_notes_present"] is True
    assert "recommendation_feedback.reason" in memory_summary.sensitive_fields_excluded
    assert "recommendation_feedback.outcome_notes" in memory_summary.sensitive_fields_excluded

    eval_case = db_session.exec(select(EvaluationCase)).one()
    assert eval_case.scenario_name == FEEDBACK_EVAL_SCENARIO
    assert eval_case.input_fixture["feedback_id"] == data["feedback_id"]
    assert eval_case.input_fixture["recommendation_id"] == str(recommendation.id)
    assert eval_case.actual_output["memory_update_status"] == "applied"
    assert eval_case.expected_properties["safety_policy_mutation_allowed"] is False
    assert eval_case.pass_fail is None
    assert DEFAULT_REASON_SIGNAL not in str(eval_case.input_fixture)
    assert DEFAULT_OUTCOME_SIGNAL not in str(eval_case.input_fixture)

    audit = db_session.exec(select(AuditEvent)).one()
    assert audit.event_type is AuditEventType.feedback_submitted
    assert audit.redaction_status.value == "redacted"
    assert audit.event_metadata["outcome_notes_present"] is True
    assert DEFAULT_OUTCOME_SIGNAL not in str(audit.event_metadata)


def test_feedback_cannot_mutate_safety_policy_or_recommendation_safety(
    db_session: Session,
) -> None:
    user = _seed_user(db_session)
    recommendation = _recommendation(db_session, user)
    original_policy_hash = hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    original_policy_version = SafetyPolicyEngine.from_default_policy().policy_version
    original_safety_status = recommendation.safety_status
    original_safety_result = dict(recommendation.safety_result)
    client = _client(db_session)

    _post_feedback(
        client,
        recommendation.id,
        rating="unsafe_or_wrong",
        action_taken="ignored",
        reason=SAFETY_REASON_SIGNAL,
        outcome_notes=SAFETY_OUTCOME_SIGNAL,
    )

    assert hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest() == original_policy_hash
    assert SafetyPolicyEngine.from_default_policy().policy_version == original_policy_version
    db_session.refresh(recommendation)
    assert recommendation.safety_status == original_safety_status
    assert recommendation.safety_result == original_safety_result
    assert recommendation.user_feedback is not None
    assert recommendation.user_feedback["safety_policy_mutation_allowed"] is False


def test_outcome_notes_are_linked_to_prior_recommendation(db_session: Session) -> None:
    user = _seed_user(db_session)
    recommendation = _recommendation(db_session, user, target_date=dt.date(2026, 1, 20))
    client = _client(db_session)

    data = _post_feedback(
        client,
        recommendation.id,
        action_taken="partially_followed",
        outcome_notes=SAFETY_OUTCOME_SIGNAL,
    )

    db_session.refresh(recommendation)
    assert recommendation.accepted_action is not None
    assert recommendation.user_feedback is not None
    outcome = recommendation.user_feedback["outcome"]
    assert outcome["linked_recommendation_id"] == str(recommendation.id)
    assert outcome["recommendation_date"] == "2026-01-20"
    assert outcome["notes_present"] is True
    assert recommendation.accepted_action["feedback_id"] == data["feedback_id"]


def test_repeated_contradicting_feedback_is_surfaced(db_session: Session) -> None:
    user = _seed_user(db_session)
    first = _recommendation(
        db_session,
        user,
        target_date=dt.date(2026, 1, 21),
        band="recovery",
        text="RECOVERY_RECOMMENDATION_A",
    )
    second = _recommendation(
        db_session,
        user,
        target_date=dt.date(2026, 1, 22),
        band="recovery",
        text="RECOVERY_RECOMMENDATION_B",
    )
    client = _client(db_session)
    reason = CONTRADICTION_REASON_SIGNAL

    first_data = _post_feedback(
        client,
        first.id,
        rating="unsafe_or_wrong",
        action_taken="ignored",
        reason=reason,
        outcome_notes=CONTRADICTION_OUTCOME_SIGNAL,
    )
    second_data = _post_feedback(
        client,
        second.id,
        rating="unsafe_or_wrong",
        action_taken="ignored",
        reason=reason,
        outcome_notes=CONTRADICTION_OUTCOME_SIGNAL,
    )

    assert first_data["contradiction_alert"] is None
    alert = second_data["contradiction_alert"]
    assert isinstance(alert, dict)
    assert alert["contradiction_key"] == "conservative_recommendation_user_felt_ready"
    assert alert["count"] == 2
    assert "surface for review" in alert["message"]

    db_session.refresh(second)
    assert second.user_feedback is not None
    wrong_because = second.user_feedback["wrong_because"]
    assert wrong_because["present"] is True
    assert wrong_because["reason_present"] is True
    assert wrong_because["reason_redaction_status"] == "redacted"
    assert wrong_because["contradicts_current_reasoning"] is True
    assert "user_reported_readiness_higher_than_reasoning" in wrong_because["categories"]
    assert second.user_feedback["contradiction_alert"]["count"] == 2
    assert reason not in str(second.user_feedback)
    eval_cases = db_session.exec(select(EvaluationCase)).all()
    assert all(reason not in str(eval_case.input_fixture) for eval_case in eval_cases)


def test_feedback_rejects_unbounded_text(db_session: Session) -> None:
    user = _seed_user(db_session)
    recommendation = _recommendation(db_session, user)
    client = _client(db_session)

    response = client.post(
        f"/v1/recommendations/{recommendation.id}/feedback",
        json={
            "rating": "useful",
            "action_taken": "followed",
            "reason": "x" * 241,
        },
    )

    assert response.status_code == 422


def test_feedback_returns_not_found_for_missing_recommendation(db_session: Session) -> None:
    _seed_user(db_session)
    client = _client(db_session)

    response = client.post(
        f"/v1/recommendations/{uuid4()}/feedback",
        json={
            "rating": "useful",
            "action_taken": "followed",
            "reason": DEFAULT_REASON_SIGNAL,
            "outcome_notes": DEFAULT_OUTCOME_SIGNAL,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "recommendation_not_found"


def test_memory_routing_failure_returns_failed_and_skips_eval(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _seed_user(db_session)
    recommendation = _recommendation(db_session, user)
    client = _client(db_session)

    def fail_latest_for_period(self: MemorySummaryRepository, **_: object) -> MemorySummary | None:
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(MemorySummaryRepository, "latest_for_period", fail_latest_for_period)

    data = _post_feedback(client, recommendation.id)

    assert data["memory_update_status"] == "failed"
    assert data["eval_queue_status"] == "skipped"
    db_session.refresh(recommendation)
    assert recommendation.user_feedback is not None
    assert recommendation.user_feedback["memory_update_status"] == "failed"
    assert recommendation.user_feedback["eval_queue_status"] == "skipped"
    assert db_session.exec(select(EvaluationCase)).all() == []


def test_eval_enqueue_failure_returns_failed_status(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _seed_user(db_session)
    recommendation = _recommendation(db_session, user)
    client = _client(db_session)

    def fail_create(
        self: EvaluationCaseRepository,
        instance: EvaluationCase,
    ) -> EvaluationCase:
        raise RuntimeError("eval unavailable")

    monkeypatch.setattr(EvaluationCaseRepository, "create", fail_create)

    data = _post_feedback(client, recommendation.id)

    assert data["memory_update_status"] == "applied"
    assert data["eval_queue_status"] == "failed"
    db_session.refresh(recommendation)
    assert recommendation.user_feedback is not None
    assert recommendation.user_feedback["eval_queue_status"] == "failed"
    assert db_session.exec(select(MemorySummary)).one()
