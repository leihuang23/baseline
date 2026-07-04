"""P3-07 assistant Q&A API tests."""

from __future__ import annotations

import datetime as dt
from collections.abc import Generator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.db.models import (
    DerivedDailyFeature,
    MemorySummary,
    ReasoningTrace,
    Recommendation,
    User,
    WorkoutSession,
)
from baseline_api.db.models.enums import (
    Modality,
    PeriodType,
    RecommendationType,
    SafetyStatus,
)
from baseline_api.db.session import get_db_session

TARGET_DATE = dt.date(2026, 7, 4)

pytestmark = pytest.mark.require_db


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5432/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )


def _client(db_session: Session) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def _seed_user(db_session: Session) -> User:
    user = User(privacy_mode="cloud_assisted", active_consent_version="v1")
    db_session.add(user)
    db_session.flush()
    return user


def _value(value: Any, unit: str = "unit") -> dict[str, Any]:
    return {"status": "computed", "value": value, "unit": unit}


def _feature_row(
    user_id: UUID,
    target_date: dt.date,
    *,
    sleep_debt: float = 0.5,
    hrv_deviation: float = 2.0,
    rhr_deviation: float = 0.0,
    load_ratio: float = 1.0,
    recovery_level: str = "high",
) -> DerivedDailyFeature:
    return DerivedDailyFeature(
        user_id=user_id,
        date=target_date,
        feature_version="test-v1",
        sleep_features={
            "values": {"sleep_debt_hours": _value(sleep_debt, "h")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        hrv_features={
            "values": {"deviation_pct": _value(hrv_deviation, "percent")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        rhr_features={
            "values": {"deviation_pct": _value(rhr_deviation, "percent")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        training_load_features={
            "values": {"acute_chronic_ratio": _value(load_ratio, "ratio")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        recovery_features={
            "values": {"level": _value(recovery_level, "category")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        goal_features={"values": {}, "data_quality": {"flags": [], "completeness": 1.0}},
        data_quality={"flags": [], "overall_completeness": 1.0},
        anomaly_flags=[],
        computed_at=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
        source_sample_ids=[],
    )


def _empty_feature_row(user_id: UUID, target_date: dt.date) -> DerivedDailyFeature:
    return DerivedDailyFeature(
        user_id=user_id,
        date=target_date,
        feature_version="test-v1",
        sleep_features={},
        hrv_features={},
        rhr_features={},
        training_load_features={},
        recovery_features={},
        goal_features={},
        data_quality={"flags": [], "overall_completeness": 0.0},
        anomaly_flags=[],
        computed_at=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
        source_sample_ids=[],
    )


def _post_query(
    client: TestClient,
    question: str,
    *,
    scopes: list[str],
    include_external_knowledge: bool = False,
) -> dict[str, Any]:
    response = client.post(
        "/v1/assistant/query",
        json={
            "question": question,
            "date_context": TARGET_DATE.isoformat(),
            "allowed_data_scope": scopes,
            "include_external_knowledge": include_external_knowledge,
            "privacy_mode": "cloud_assisted",
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_recent_history_answer_is_sql_grounded(db_session: Session) -> None:
    user = _seed_user(db_session)
    feature_ids: list[str] = []
    for offset in range(7):
        feature = _feature_row(
            user.id,
            TARGET_DATE - dt.timedelta(days=offset),
            sleep_debt=offset / 10,
        )
        db_session.add(feature)
        db_session.flush()
        feature_ids.append(str(feature.id))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(client, "How has my sleep looked recently?", scopes=["recent_health"])

    assert "Sleep debt averaged" in data["answer"]
    assert data["personal_evidence"]
    assert {item["source"] for item in data["personal_evidence"]} == {"derived_daily_feature"}
    assert data["safety_status"] == "passed"
    assert data["trace_id"]
    trace = db_session.get(ReasoningTrace, UUID(data["trace_id"]))
    assert trace is not None
    retrieval = trace.trace_payload["assistant_queries"][0]["retrieval"]
    assert retrieval["table"] == "derived_daily_feature"
    assert set(retrieval["row_ids"]) == set(feature_ids)
    assert retrieval["metric_path"] == "sleep_features.values.sleep_debt_hours.value"


def test_compare_periods_answer_uses_two_sql_windows(db_session: Session) -> None:
    user = _seed_user(db_session)
    current_ids: list[str] = []
    previous_ids: list[str] = []
    for offset in range(3):
        current = _feature_row(
            user.id,
            TARGET_DATE - dt.timedelta(days=offset),
            recovery_level="high",
        )
        previous = _feature_row(
            user.id,
            TARGET_DATE - dt.timedelta(days=30 + offset),
            recovery_level="low",
        )
        db_session.add(current)
        db_session.add(previous)
        db_session.flush()
        current_ids.append(str(current.id))
        previous_ids.append(str(previous.id))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(client, "How did recovery change this month?", scopes=["recent_health"])

    assert "Recovery level increased" in data["answer"]
    assert {item["metric"] for item in data["personal_evidence"]} >= {
        "recovery_level.current_average",
        "recovery_level.previous_average",
        "recovery_level.delta",
    }
    assert data["confidence"] == "medium"
    trace = db_session.get(ReasoningTrace, UUID(data["trace_id"]))
    assert trace is not None
    retrieval = trace.trace_payload["assistant_queries"][0]["retrieval"]
    assert retrieval["table"] == "derived_daily_feature"
    assert set(retrieval["current_row_ids"]) == set(current_ids)
    assert set(retrieval["previous_row_ids"]) == set(previous_ids)


def test_modality_answer_uses_workout_sessions(db_session: Session) -> None:
    user = _seed_user(db_session)
    workout_ids: list[str] = []
    for day in (TARGET_DATE, TARGET_DATE - dt.timedelta(days=2)):
        workout = WorkoutSession(
            user_id=user.id,
            start_time=dt.datetime.combine(day, dt.time(hour=7), tzinfo=dt.UTC),
            end_time=dt.datetime.combine(day, dt.time(hour=8), tzinfo=dt.UTC),
            modality=Modality.run,
            distance=10_000,
            duration=3600,
            active_energy=650,
            average_hr=145,
            max_hr=170,
            normalization_version="test-v1",
            source_sample_ids=[],
        )
        db_session.add(workout)
        db_session.flush()
        workout_ids.append(str(workout.id))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(client, "How did my running look this week?", scopes=["recent_health"])

    assert "2 sessions" in data["answer"]
    assert {item["metric"] for item in data["personal_evidence"]} >= {
        "run_workout_count",
        "run_duration_minutes",
    }
    trace = db_session.get(ReasoningTrace, UUID(data["trace_id"]))
    assert trace is not None
    retrieval = trace.trace_payload["assistant_queries"][0]["retrieval"]
    assert retrieval["table"] == "workout_session"
    assert set(retrieval["row_ids"]) == set(workout_ids)


def test_memory_summary_answer_is_grounded_and_traced(db_session: Session) -> None:
    user = _seed_user(db_session)
    memory = MemorySummary(
        user_id=user.id,
        period_type=PeriodType.weekly,
        start_date=TARGET_DATE - dt.timedelta(days=6),
        end_date=TARGET_DATE,
        summary_version="test-v1",
        observations=[
            {
                "observation": (
                    "Easy aerobic days after short sleep were followed by steadier recovery."
                )
            }
        ],
        hypotheses=[],
        confidence=0.8,
        source_refs=[{"table": "derived_daily_feature"}],
        sensitive_fields_excluded=[],
    )
    db_session.add(memory)
    db_session.commit()
    client = _client(db_session)

    data = _post_query(
        client,
        "What pattern did you learn about me?",
        scopes=["memory"],
    )

    assert "Easy aerobic days" in data["answer"]
    assert data["personal_evidence"][0]["source"] == f"memory_summary:{memory.id}"
    trace = db_session.get(ReasoningTrace, UUID(data["trace_id"]))
    assert trace is not None
    retrieval = trace.trace_payload["assistant_queries"][0]["retrieval"]
    assert retrieval["table"] == "memory_summary"
    assert str(memory.id) in retrieval["row_ids"]


def test_insufficient_data_is_disclosed(db_session: Session) -> None:
    _seed_user(db_session)
    db_session.commit()
    client = _client(db_session)

    data = _post_query(client, "How has my HRV looked recently?", scopes=["recent_health"])

    assert data["answer"].startswith("Not enough data")
    assert data["personal_evidence"][0]["value"] == "not_enough_data"
    assert data["confidence"] == "low"


def test_unsupported_metric_returns_not_enough_data(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(_feature_row(user.id, TARGET_DATE, recovery_level="high"))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(client, "How did hydration change this month?", scopes=["recent_health"])

    assert data["answer"].startswith("Not enough data")
    assert data["personal_evidence"][0]["metric"] == "supported_metric"
    assert data["personal_evidence"][0]["value"] == "not_enough_data"
    assert data["confidence"] == "low"


def test_sparse_recent_history_has_low_confidence(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(_feature_row(user.id, TARGET_DATE, sleep_debt=0.7))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(client, "How has my sleep looked recently?", scopes=["recent_health"])

    assert "Sleep debt averaged" in data["answer"]
    assert data["confidence"] == "low"
    assert any("Only 1 of 7 days" in item for item in data["uncertainty"])


def test_diagnosis_request_is_refused_by_safety_gate(db_session: Session) -> None:
    _seed_user(db_session)
    db_session.commit()
    client = _client(db_session)

    data = _post_query(client, "Do I have anemia based on my HRV?", scopes=["recent_health"])

    assert data["safety_status"] == "blocked"
    assert "cannot diagnose" in data["answer"]
    assert data["personal_evidence"][0]["metric"] == "safety_policy"


def test_treatment_request_is_refused_by_safety_gate(db_session: Session) -> None:
    _seed_user(db_session)
    db_session.commit()
    client = _client(db_session)

    data = _post_query(client, "Should I take magnesium tonight?", scopes=["recent_health"])

    assert data["safety_status"] == "blocked"
    assert "wellness decision support" in data["answer"]
    assert data["personal_evidence"][0]["metric"] == "safety_policy"


def test_unsafe_memory_evidence_is_not_returned(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(
        MemorySummary(
            user_id=user.id,
            period_type=PeriodType.weekly,
            start_date=TARGET_DATE - dt.timedelta(days=6),
            end_date=TARGET_DATE,
            summary_version="test-v1",
            observations=[{"observation": "You have anemia after low HRV days."}],
            hypotheses=[],
            confidence=0.7,
            source_refs=[],
            sensitive_fields_excluded=[],
        )
    )
    db_session.commit()
    client = _client(db_session)

    data = _post_query(
        client,
        "What pattern did you learn about me?",
        scopes=["memory"],
    )

    assert data["safety_status"] == "rewritten"
    assert "anemia" not in data["answer"].lower()
    assert data["personal_evidence"][0]["metric"] == "safety_policy"
    assert "anemia" not in data["personal_evidence"][0]["interpretation"].lower()


def test_external_knowledge_seam_keeps_sources_separate(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(_feature_row(user.id, TARGET_DATE, sleep_debt=0.2))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(
        client,
        "How has my sleep looked recently?",
        scopes=["recent_health", "external_knowledge"],
        include_external_knowledge=True,
    )

    assert data["personal_evidence"]
    assert data["external_sources"] == []
    assert any("External retrieval is not implemented" in item for item in data["uncertainty"])


def test_plan_for_week_is_framed_as_candidate_not_prescription(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(_feature_row(user.id, TARGET_DATE, sleep_debt=0.4, recovery_level="high"))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(
        client,
        "Create a plan for this week",
        scopes=["recent_health", "goals", "briefing_trace"],
    )

    assert data["answer"].startswith("Candidate plan, not a prescription")
    assert "prescription" in data["uncertainty"][0]
    assert data["personal_evidence"]


def test_plan_for_week_requires_usable_personal_evidence(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(_empty_feature_row(user.id, TARGET_DATE))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(
        client,
        "Create a plan for this week",
        scopes=["recent_health", "goals", "briefing_trace"],
    )

    assert data["answer"].startswith("Not enough data")
    assert data["personal_evidence"][0]["metric"] == "candidate_plan"
    assert data["personal_evidence"][0]["value"] == "not_enough_data"
    assert data["confidence"] == "low"


def test_plan_for_week_rejects_stale_recent_features(db_session: Session) -> None:
    user = _seed_user(db_session)
    db_session.add(_feature_row(user.id, TARGET_DATE - dt.timedelta(days=14)))
    db_session.commit()
    client = _client(db_session)

    data = _post_query(
        client,
        "Create a plan for this week",
        scopes=["recent_health", "goals", "briefing_trace"],
    )

    assert data["answer"].startswith("Not enough data")
    assert data["personal_evidence"][0]["metric"] == "candidate_plan"
    assert data["personal_evidence"][0]["value"] == "not_enough_data"
    assert data["confidence"] == "low"


def test_today_briefing_followup_reuses_reasoning_trace(db_session: Session) -> None:
    user = _seed_user(db_session)
    trace_id = uuid4()
    db_session.add(
        ReasoningTrace(
            id=trace_id,
            user_id=user.id,
            date=TARGET_DATE,
            trace_version="test-v1",
            assessment_version="test-v1",
            input_hash="input-hash",
            rules_fired=[],
            hard_safety_flags=[],
            trace_payload={"briefing_generation": {"status": "success"}},
        )
    )
    db_session.add(
        Recommendation(
            user_id=user.id,
            date=TARGET_DATE,
            recommendation_type=RecommendationType.training,
            recommendation_text="Keep today moderate instead of tempo.",
            candidate_options=[],
            evidence_refs=[],
            safety_status=SafetyStatus.passed,
            safety_result={"status": "passed"},
            reasoning_trace_id=trace_id,
            briefing_payload={
                "evidence": [
                    {
                        "metric": "sleep_debt_hours",
                        "value": 2.1,
                        "interpretation": "unfavorable",
                        "source": "sleep_features.values.sleep_debt_hours",
                    }
                ],
                "recommendation": {"primary": "Keep today moderate instead of tempo."},
                "risk_flags": ["high_sleep_debt"],
            },
        )
    )
    db_session.commit()
    client = _client(db_session)

    data = _post_query(
        client,
        "Why not tempo today?",
        scopes=["briefing_trace", "recent_health"],
    )

    assert data["trace_id"] == str(trace_id)
    assert "high_sleep_debt" in data["answer"]
    trace = db_session.get(ReasoningTrace, trace_id)
    assert trace is not None
    assert trace.trace_payload["assistant_queries"][0]["question"] == "Why not tempo today?"


def test_briefing_followup_does_not_reuse_stale_trace(db_session: Session) -> None:
    user = _seed_user(db_session)
    stale_trace_id = uuid4()
    stale_date = TARGET_DATE - dt.timedelta(days=1)
    db_session.add(
        ReasoningTrace(
            id=stale_trace_id,
            user_id=user.id,
            date=stale_date,
            trace_version="test-v1",
            assessment_version="test-v1",
            input_hash="input-hash",
            rules_fired=[],
            hard_safety_flags=[],
            trace_payload={"briefing_generation": {"status": "success"}},
        )
    )
    db_session.add(
        Recommendation(
            user_id=user.id,
            date=stale_date,
            recommendation_type=RecommendationType.training,
            recommendation_text="Yesterday was easy.",
            candidate_options=[],
            evidence_refs=[],
            safety_status=SafetyStatus.passed,
            safety_result={"status": "passed"},
            reasoning_trace_id=stale_trace_id,
            briefing_payload={
                "evidence": [
                    {
                        "metric": "sleep_debt_hours",
                        "value": 1.5,
                        "interpretation": "moderate",
                        "source": "sleep_features.values.sleep_debt_hours",
                    }
                ],
                "recommendation": {"primary": "Yesterday was easy."},
                "risk_flags": [],
            },
        )
    )
    db_session.commit()
    client = _client(db_session)

    data = _post_query(
        client,
        "Why not tempo today?",
        scopes=["briefing_trace", "recent_health"],
    )

    assert data["answer"].startswith("Not enough data")
    assert data["trace_id"] != str(stale_trace_id)
