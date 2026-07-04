"""P3-06 daily briefing assembly API tests."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Generator
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from baseline_api.app import create_app
from baseline_api.briefing.service import _enforce_served_briefing_safety
from baseline_api.config import Settings
from baseline_api.db.models import (
    ConsentRecord,
    DailyAnalysisJob,
    DailyCheckIn,
    DerivedDailyFeature,
    Goal,
    MemorySummary,
    ModelRun,
    ReasoningTrace,
    Recommendation,
    User,
)
from baseline_api.db.models.enums import (
    GoalCategory,
    PeriodType,
    RunType,
    SensitiveNotePolicy,
    TimeHorizon,
)
from baseline_api.db.session import get_db_session
from baseline_api.llm.orchestrator import OrchestratorResult
from baseline_api.llm.schemas import LLMExplanationOutput
from baseline_api.observability import metrics
from baseline_api.safety.engine import SafetyPolicyEngine
from baseline_api.schemas.api import DailyBriefingResponse

TARGET_DATE = dt.date(2026, 7, 4)


class FakeLLMExplainer:
    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    async def explain(self, **_: Any) -> OrchestratorResult:
        model_run = ModelRun(
            user_id=_["user_id"],
            run_type=RunType.daily_briefing,
            model_provider="fake",
            model_name="fake-briefing-model",
            prompt_version="test-v1",
            input_hash="input-hash",
            output_hash="output-hash",
            schema_version="llm_explanation_v1",
            token_usage={"total": 12},
            cost=0.02,
            latency_ms=25,
            safety_result={"status": "passed"},
        )
        self._session.add(model_run)
        self._session.flush()
        return OrchestratorResult(
            output=LLMExplanationOutput(
                summary="Use a moderate training day with an easy escape hatch.",
                rationale=["Sleep and cardiovascular signals are usable today."],
                uncertainty=["Subjective recovery can still change the safest choice."],
                personal_evidence_refs=["sleep_debt_hours"],
                external_citations=[],
                safety_boundary_acknowledged=True,
                no_diagnosis_or_treatment_claims=True,
            ),
            model_runs=[model_run],
        )


class RiskyLLMExplainer:
    async def explain(self, **_: Any) -> OrchestratorResult:
        return OrchestratorResult(
            output=LLMExplanationOutput(
                summary="You are overtrained.",
                rationale=["This unsafe wording should be rewritten."],
                uncertainty=["Safety rewrite should preserve uncertainty."],
                personal_evidence_refs=["deterministic_assessment"],
                external_citations=[],
                safety_boundary_acknowledged=True,
                no_diagnosis_or_treatment_claims=True,
            )
        )


class UnsafeCitationLLMExplainer:
    async def explain(self, **_: Any) -> OrchestratorResult:
        return OrchestratorResult(
            output=LLMExplanationOutput(
                summary="Use a moderate training day with an easy escape hatch.",
                rationale=["Sleep and cardiovascular signals are usable today."],
                uncertainty=["Subjective recovery can still change the safest choice."],
                personal_evidence_refs=["sleep_debt_hours"],
                external_citations=[
                    {
                        "title": "Unsafe certainty",
                        "source": "fixture",
                        "url": None,
                        "cited_claim": "Your diagnosis is overtrained.",
                    }
                ],
                safety_boundary_acknowledged=True,
                no_diagnosis_or_treatment_claims=True,
            )
        )


class FailingLLMExplainer:
    async def explain(self, **_: Any) -> OrchestratorResult:
        raise RuntimeError("provider down")


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )


def _client(
    db_session: Session,
    *,
    llm_explainer: object | None = None,
    run_inline: bool = True,
) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    app.state.briefing_run_inline = run_inline
    if llm_explainer is not None:
        app.state.briefing_llm_explainer = llm_explainer
    return TestClient(app)


def _value(value: Any, unit: str = "unit") -> dict[str, Any]:
    return {"status": "computed", "value": value, "unit": unit}


def _seed_fixture_day(db_session: Session) -> User:
    user = User(
        privacy_mode="cloud_assisted",
        active_consent_version="v1",
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=["all"],
            cloud_processing_enabled=True,
            external_llm_enabled=True,
            raw_note_processing_enabled=False,
            timestamp=dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        )
    )
    db_session.add(
        Goal(
            user_id=user.id,
            category=GoalCategory.vo2_max,
            priority=4,
            time_horizon=TimeHorizon.medium_term,
            success_metric="Improve aerobic capacity",
            constraints={},
            active=True,
        )
    )
    db_session.add(
        DailyCheckIn(
            user_id=user.id,
            date=TARGET_DATE,
            energy_score=7,
            mood_score=7,
            soreness_score=3,
            stress_score=3,
            perceived_recovery_score=8,
            food_quality_score=7,
            alcohol_flag=False,
            illness_flag=False,
            injury_flag=False,
            travel_flag=False,
            sensitive_note_policy=SensitiveNotePolicy.exclude_from_external_llm,
            structured_notes={"training_context": "normal"},
        )
    )
    db_session.add(_feature_row(user.id, TARGET_DATE))
    db_session.flush()
    return user


def _feature_row(user_id: UUID, target_date: dt.date) -> DerivedDailyFeature:
    return DerivedDailyFeature(
        user_id=user_id,
        date=target_date,
        feature_version="test-v1",
        sleep_features={
            "values": {"sleep_debt_hours": _value(0.4, "h")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        hrv_features={
            "values": {"deviation_pct": _value(4.0, "percent")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        rhr_features={
            "values": {
                "deviation_pct": _value(-1.0, "percent"),
                "deviation_bpm": _value(-1.0, "bpm"),
            },
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        training_load_features={
            "values": {
                "acute_chronic_ratio": _value(1.0, "ratio"),
                "load_balance": _value("balanced", "category"),
                "density_by_modality": _value(
                    {"run": {"status": "computed", "value": 1, "unit": "sessions"}},
                    "structured",
                ),
            },
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        recovery_features={
            "values": {"level": _value("high", "category")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        goal_features={
            "values": {},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        data_quality={
            "flags": [],
            "overall_completeness": 1.0,
            "section_completeness": {},
        },
        anomaly_flags=[],
        computed_at=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
        source_sample_ids=[],
    )


def _generate(
    client: TestClient,
    *,
    privacy_mode: str = "cloud_assisted",
    date: dt.date = TARGET_DATE,
) -> dict[str, Any]:
    response = client.post(
        "/v1/analysis/daily",
        json={
            "date": date.isoformat(),
            "force_recompute": False,
            "include_external_knowledge": False,
            "privacy_mode": privacy_mode,
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_fixture_day_produces_complete_persisted_briefing(db_session: Session) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=FakeLLMExplainer(db_session))

    job = _generate(client)
    assert job["status"] == "completed"

    response = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}")

    assert response.status_code == 200
    briefing = response.json()["data"]
    assert briefing["readiness_state"]
    assert briefing["confidence"]
    assert briefing["data_freshness"]
    assert briefing["evidence"]
    assert briefing["recommendation"]["primary"] == (
        "Use a moderate training day with an easy escape hatch."
    )
    assert briefing["recommendation_band"]
    assert briefing["candidate_options"]
    assert briefing["goal_tradeoffs"]
    assert briefing["uncertainty"]
    assert briefing["data_quality_notes"]
    assert briefing["what_would_change_my_mind"]
    assert briefing["safety_notes"]
    assert briefing["safety_status"] == "passed"
    assert briefing["trace_id"]
    assert briefing["generated_at"]

    recommendation = db_session.exec(select(Recommendation)).one()
    assert recommendation.briefing_payload["trace_id"] == briefing["trace_id"]
    assert recommendation.reasoning_trace_id == UUID(briefing["trace_id"])
    trace = db_session.get(ReasoningTrace, UUID(briefing["trace_id"]))
    assert trace is not None
    assert trace.trace_payload["briefing_generation"]["total_cost"] == 0.02
    assert trace.trace_payload["briefing_generation"]["within_p95_target"] is True
    stages = trace.trace_payload["briefing_generation"]["stages"]
    assert {stage["trace_id"] for stage in stages} == {briefing["trace_id"]}
    assert [stage["stage"] for stage in stages if stage["stage"] != "enqueue"] == [
        "job_running",
        "features",
        "data_freshness",
        "retrieval",
        "reasoning",
        "llm_explanation",
        "safety",
        "memory",
        "persistence",
    ]
    stored = recommendation.briefing_payload
    for field in (
        "readiness_state",
        "confidence",
        "data_freshness",
        "evidence",
        "recommendation_band",
        "candidate_options",
        "goal_tradeoffs",
        "uncertainty",
        "safety_notes",
        "trace_id",
        "generated_at",
        "what_would_change_my_mind",
    ):
        assert stored[field]
    persisted_job = db_session.get(DailyAnalysisJob, UUID(job["analysis_job_id"]))
    assert persisted_job is not None
    assert {stage["trace_id"] for stage in persisted_job.stage_trace} == {briefing["trace_id"]}
    summaries = list(db_session.exec(select(MemorySummary)).all())
    assert {summary.period_type for summary in summaries} == {PeriodType.daily, PeriodType.weekly}
    daily_summary = next(
        summary for summary in summaries if summary.period_type == PeriodType.daily
    )
    weekly_summary = next(
        summary for summary in summaries if summary.period_type == PeriodType.weekly
    )
    assert any(ref["table"] == "recommendation" for ref in daily_summary.source_refs)
    assert any(
        ref["table"] == "memory_summary" and ref["id"] == str(daily_summary.id)
        for ref in weekly_summary.source_refs
    )
    job_status = client.get(f"/v1/analysis/daily/{job['analysis_job_id']}").json()["data"]
    assert job_status["status"] == "completed"

    metric_text = metrics.generate_latest(metrics.registry).decode()
    assert 'baseline_llm_generation_total{status="success"}' in metric_text
    assert 'baseline_llm_cost_total{model="fake-briefing-model"}' in metric_text
    assert "baseline_briefing_latency_seconds_count" in metric_text


def test_trace_endpoint_returns_read_only_inspection(db_session: Session) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=FakeLLMExplainer(db_session))

    _generate(client)
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]
    response = client.get(f"/v1/analysis/traces/{briefing['trace_id']}")

    assert response.status_code == 200
    trace = response.json()["data"]
    assert trace["trace_id"] == briefing["trace_id"]
    assert trace["data_freshness"] == briefing["data_freshness"]
    assert trace["feature_values"]
    assert trace["rules_fired"]
    assert trace["retrieved_memory"] == briefing["memory_observations"]
    assert trace["external_sources"] == briefing["external_citations"]
    assert trace["model_metadata"]["briefing_generation_status"] == "success"
    assert trace["model_metadata"]["model_run_ids"]


def test_daily_briefing_uses_recent_memory_summaries_for_reasoning(
    db_session: Session,
) -> None:
    user = _seed_fixture_day(db_session)
    db_session.add(
        MemorySummary(
            user_id=user.id,
            period_type=PeriodType.weekly,
            start_date=TARGET_DATE - dt.timedelta(days=7),
            end_date=TARGET_DATE - dt.timedelta(days=1),
            summary_version="memory-summary-v1",
            observations=[
                {
                    "kind": "observation",
                    "key": "weekly_readiness_arc",
                    "text": "Illness disrupted two recent training days.",
                    "confidence": 0.8,
                    "source_refs": [{"table": "daily_check_in", "id": "checkin-prior"}],
                }
            ],
            hypotheses=[],
            confidence=0.8,
            source_refs=[{"table": "daily_check_in", "id": "checkin-prior"}],
            sensitive_fields_excluded=[],
        )
    )
    db_session.flush()
    client = _client(db_session)

    _generate(client, privacy_mode="local_only")
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]
    trace = db_session.exec(select(ReasoningTrace)).one()

    assert briefing["memory_observations"]
    assert briefing["memory_observations"][0]["observation"] == (
        "Illness disrupted two recent training days."
    )
    assert any(item["rule_id"] == "memory_recent_disruption" for item in trace.rules_fired)


def test_served_safety_preserves_memory_on_disclaimer_only_rewrite() -> None:
    briefing = DailyBriefingResponse.model_validate(
        {
            "date": TARGET_DATE,
            "readiness_state": "mixed",
            "confidence": "medium",
            "data_freshness": {},
            "evidence": [
                {
                    "metric": "illness_flag",
                    "value": True,
                    "interpretation": "Used as a wellness readiness signal.",
                }
            ],
            "memory_observations": [
                {
                    "observation": "Illness disrupted two recent training days.",
                    "relevance": "Recent structured memory summary for readiness continuity.",
                    "period": "2026-06-27..2026-07-03",
                }
            ],
            "risk_flags": [],
            "recommendation": {"primary": "Keep the day easy and reversible."},
            "recommendation_band": "easy_or_recovery",
            "uncertainty": ["Illness flag is user-reported."],
            "safety_status": "passed",
            "safety_notes": ["Baseline is wellness decision support, not medical advice."],
            "trace_id": "11111111-1111-1111-1111-111111111111",
            "generated_at": "2026-07-04T08:00:00Z",
        }
    )

    enforced = _enforce_served_briefing_safety(
        SafetyPolicyEngine.from_default_policy(),
        briefing,
    )

    assert enforced.safety_status.value == "rewritten"
    assert enforced.memory_observations == briefing.memory_observations
    assert "qualified clinician" in " ".join(enforced.safety_notes)


def test_daily_analysis_post_returns_queued_job_by_default(
    db_session: Session,
    monkeypatch: Any,
) -> None:
    _seed_fixture_day(db_session)

    async def noop_background_job(*_: Any) -> None:
        return None

    monkeypatch.setattr(
        "baseline_api.api.v1.contracts._run_daily_analysis_job",
        noop_background_job,
    )
    client = _client(db_session, run_inline=False)

    job = _generate(client)

    assert job["status"] == "queued"
    persisted_job = db_session.get(DailyAnalysisJob, UUID(job["analysis_job_id"]))
    assert persisted_job is not None
    assert persisted_job.status == "queued"


def test_llm_failure_degrades_to_deterministic_briefing(db_session: Session) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=FailingLLMExplainer())

    _generate(client)
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert "deterministic" in briefing["recommendation"]["primary"].lower()
    trace = db_session.exec(select(ReasoningTrace)).one()
    generation = trace.trace_payload["briefing_generation"]
    assert generation["status"] == "degraded"
    assert generation["degrade_reason"] == "RuntimeError"


def test_last_briefing_can_be_served_for_offline_view(db_session: Session) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=FakeLLMExplainer(db_session))
    _generate(client)

    missing = client.get("/v1/briefings/2026-07-05")
    offline = client.get("/v1/briefings/2026-07-05?offline_last=true")

    assert missing.status_code == 404
    assert offline.status_code == 200
    assert offline.json()["data"]["date"] == TARGET_DATE.isoformat()


def test_external_knowledge_disabled_and_local_only_still_serves_briefing(
    db_session: Session,
) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session)

    _generate(client, privacy_mode="local_only")
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert briefing["external_citations"] == []
    assert "deterministic" in briefing["recommendation"]["primary"].lower()
    trace = db_session.exec(select(ReasoningTrace)).one()
    generation = trace.trace_payload["briefing_generation"]
    assert generation["status"] == "degraded"
    assert generation["degrade_reason"] == "privacy_mode_local_only"


def test_retrieval_degraded_mode_still_persists_deterministic_briefing(
    db_session: Session,
    monkeypatch: Any,
) -> None:
    _seed_fixture_day(db_session)
    original_exec = db_session.exec
    original_rollback = db_session.rollback
    retrieval_failed = False
    rollback_called = False

    def flaky_exec(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal retrieval_failed
        if (
            not retrieval_failed
            and "FROM memory_summary" in str(statement)
            and "memory_summary.end_date <" in str(statement)
        ):
            retrieval_failed = True
            raise RuntimeError("forced_retrieval_failure")
        return original_exec(statement, *args, **kwargs)

    def rollback_spy() -> None:
        nonlocal rollback_called
        rollback_called = True
        original_rollback()

    monkeypatch.setattr(db_session, "exec", flaky_exec)
    monkeypatch.setattr(db_session, "rollback", rollback_spy)
    client = _client(db_session)

    _generate(client, privacy_mode="local_only")
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert retrieval_failed is True
    assert rollback_called is False
    assert briefing["evidence"]
    assert any("retrieval" in item.lower() for item in briefing["uncertainty"])
    trace = db_session.exec(select(ReasoningTrace)).one()
    generation = trace.trace_payload["briefing_generation"]
    assert generation["retrieval_degraded"] is True
    assert generation["retrieval_degrade_reason"] == "RuntimeError"


def test_safety_gate_runs_and_removes_medical_certainty_language(db_session: Session) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=RiskyLLMExplainer())

    _generate(client)
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert briefing["safety_notes"]
    assert briefing["safety_status"] == "rewritten"
    assert "overtrained" not in briefing["recommendation"]["primary"].lower()
    rows = db_session.exec(select(Recommendation).order_by(col(Recommendation.created_at))).all()
    assert rows[-1].safety_status.value == "rewritten"


def test_final_safety_gate_covers_side_fields_before_persistence(
    db_session: Session,
) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=UnsafeCitationLLMExplainer())

    _generate(client)
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    served_text = json.dumps(briefing).lower()
    assert briefing["safety_status"] == "rewritten"
    assert briefing["external_citations"] == []
    assert "overtrained" not in served_text
    assert "diagnosis is" not in served_text
    recommendation = db_session.exec(select(Recommendation)).one()
    stored_text = json.dumps(recommendation.briefing_payload).lower()
    assert "overtrained" not in stored_text
    assert "diagnosis is" not in stored_text
