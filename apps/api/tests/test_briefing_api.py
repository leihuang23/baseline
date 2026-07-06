"""P3-06 daily briefing assembly API tests."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Generator, Sequence
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from packages.knowledge.models import KnowledgeSourceDocument
from packages.knowledge.pipeline import KnowledgeIngestionPipeline
from packages.knowledge.store import SQLModelKnowledgeVectorStore
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from baseline_api.app import create_app
from baseline_api.briefing.service import (
    BriefingError,
    DailyBriefingService,
    RetrievalResult,
    _combine_retrieval,
    _completed_job_ordering,
    _enforce_served_briefing_safety,
    _external_citations_from_retrieval,
)
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
    KnowledgeSourceType,
    PeriodType,
    RunType,
    SensitiveNotePolicy,
    TimeHorizon,
    TrustLevel,
)
from baseline_api.db.models.knowledge import KnowledgeChunk, KnowledgeSource
from baseline_api.db.session import get_db_session
from baseline_api.llm.orchestrator import OrchestratorResult
from baseline_api.llm.schemas import LLMExplanationOutput
from baseline_api.observability import metrics
from baseline_api.observability.alerts import stale_briefing_alert
from baseline_api.retrieval import (
    KnowledgeChunkHit,
    KnowledgeRetrievalResult,
    KnowledgeRetrievalService,
    build_external_knowledge_query,
)
from baseline_api.safety.engine import SafetyPolicyEngine
from baseline_api.schemas.api import (
    DailyAnalysisRequest,
    DailyAnalysisResponse,
    DailyBriefingResponse,
)
from baseline_api.schemas.enums import AnalysisJobStatus, PrivacyMode
from baseline_api.schemas.recommendation import RecommendationContract
from baseline_api.worker import WorkerSettings

TARGET_DATE = dt.date(2026, 7, 4)


def _minimal_briefing() -> DailyBriefingResponse:
    return DailyBriefingResponse.model_validate(
        {
            "date": TARGET_DATE.isoformat(),
            "readiness_state": "moderate",
            "confidence": "medium",
            "data_freshness": {},
            "evidence": [
                {
                    "metric": "deterministic_assessment",
                    "value": "available",
                    "interpretation": "The briefing is based on deterministic readiness rules.",
                    "source": "reasoning_engine",
                }
            ],
            "recommendation": {"primary": "Use a moderate training day."},
            "recommendation_band": "moderate",
            "uncertainty": ["Normal day-to-day variability still applies."],
            "safety_notes": ["Baseline is wellness decision support, not medical advice."],
            "trace_id": str(uuid4()),
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        }
    )


def test_retrieved_external_hits_bind_when_llm_emits_no_claims() -> None:
    hit = KnowledgeChunkHit(
        chunk_id=uuid4(),
        source_id=uuid4(),
        source_version="v1",
        chunk_index=0,
        text=(
            "General research on daily readiness, training recovery, and sleep describes broad "
            "non-personalized patterns for conservative training choices."
        ),
        relevance_score=1.0,
        title="General Training Recovery Reference",
        source="Baseline Test Curation Board",
        url_or_identifier="https://example.org/general-training-recovery",
        trust_level="authoritative",
    )
    retrieval = RetrievalResult(
        observations=[],
        trace_items=[],
        external_hits=[hit],
    )

    citations = _external_citations_from_retrieval([], retrieval)

    assert [citation.title for citation in citations] == ["General Training Recovery Reference"]
    assert "General research (non-personalized)" in citations[0].cited_claim


def test_prompt_external_knowledge_binds_when_retrieval_hits_are_missing() -> None:
    hit = KnowledgeChunkHit(
        chunk_id=uuid4(),
        source_id=uuid4(),
        source_version="v1",
        chunk_index=0,
        text=(
            "General research on daily readiness, training recovery, and sleep describes broad "
            "non-personalized patterns for conservative training choices."
        ),
        relevance_score=1.0,
        title="General Training Recovery Reference",
        source="Baseline Test Curation Board",
        url_or_identifier="https://example.org/general-training-recovery",
        trust_level="authoritative",
    )
    retrieval = RetrievalResult(
        observations=[],
        trace_items=[],
        external_knowledge=[hit.to_prompt_dict()],
    )

    citations = _external_citations_from_retrieval([], retrieval)

    assert [citation.title for citation in citations] == ["General Training Recovery Reference"]
    assert "General research (non-personalized)" in citations[0].cited_claim


def test_unsupported_llm_external_claim_does_not_drop_retrieved_citation() -> None:
    hit = KnowledgeChunkHit(
        chunk_id=uuid4(),
        source_id=uuid4(),
        source_version="v1",
        chunk_index=0,
        text=(
            "General research on daily readiness, training recovery, and sleep describes broad "
            "non-personalized patterns for conservative training choices."
        ),
        relevance_score=1.0,
        title="General Training Recovery Reference",
        source="Baseline Test Curation Board",
        url_or_identifier="https://example.org/general-training-recovery",
        trust_level="authoritative",
    )
    retrieval = RetrievalResult(
        observations=[],
        trace_items=[],
        external_hits=[hit],
    )

    citations = _external_citations_from_retrieval(
        [
            {
                "title": "Unsupported",
                "source": "llm",
                "url": None,
                "cited_claim": "General research (non-personalized): Creatine cures anemia.",
            }
        ],
        retrieval,
    )

    assert [citation.title for citation in citations] == ["General Training Recovery Reference"]
    assert "Creatine cures anemia" not in citations[0].cited_claim


def test_combined_retrieval_binds_citations_from_external_hits() -> None:
    hit = KnowledgeChunkHit(
        chunk_id=uuid4(),
        source_id=uuid4(),
        source_version="v1",
        chunk_index=0,
        text=(
            "General research on daily readiness, training recovery, and sleep describes broad "
            "non-personalized patterns for conservative training choices."
        ),
        relevance_score=1.0,
        title="General Training Recovery Reference",
        source="Baseline Test Curation Board",
        url_or_identifier="https://example.org/general-training-recovery",
        trust_level="authoritative",
    )

    combined = _combine_retrieval(
        RetrievalResult(observations=[], trace_items=[]),
        KnowledgeRetrievalResult(
            hits=[hit],
            citations=[],
            external_knowledge=[hit.to_prompt_dict()],
            uncertainty=[],
            citation_accuracy=0.0,
        ),
    )

    assert combined.external_citations
    assert combined.external_citations[0].title == "General Training Recovery Reference"
    assert "General research (non-personalized)" in combined.external_citations[0].cited_claim
    assert combined.citation_accuracy == 1.0


def test_combined_retrieval_binds_citations_from_external_prompt_payload() -> None:
    hit = KnowledgeChunkHit(
        chunk_id=uuid4(),
        source_id=uuid4(),
        source_version="v1",
        chunk_index=0,
        text=(
            "General research on daily readiness, training recovery, and sleep describes broad "
            "non-personalized patterns for conservative training choices."
        ),
        relevance_score=1.0,
        title="General Training Recovery Reference",
        source="Baseline Test Curation Board",
        url_or_identifier="https://example.org/general-training-recovery",
        trust_level="authoritative",
    )

    combined = _combine_retrieval(
        RetrievalResult(observations=[], trace_items=[]),
        KnowledgeRetrievalResult(
            hits=[],
            citations=[],
            external_knowledge=[hit.to_prompt_dict()],
            uncertainty=[],
            citation_accuracy=0.0,
        ),
    )

    assert [citation.title for citation in combined.external_citations] == [
        "General Training Recovery Reference"
    ]
    assert combined.external_hits
    assert combined.citation_accuracy == 1.0


def test_external_retrieval_db_error_does_not_fallback_to_lexical(
    monkeypatch: Any,
) -> None:
    service = KnowledgeRetrievalService(cast(Session, object()))

    def fake_chunk_pairs(
        *,
        require_embedding: bool,
    ) -> list[tuple[KnowledgeChunk, KnowledgeSource]]:
        _ = require_embedding
        return []

    def fail_vector_query(
        query: str,
        query_embedding: Sequence[float],
        pairs: Sequence[tuple[KnowledgeChunk, KnowledgeSource]],
        *,
        limit: int,
    ) -> list[KnowledgeChunkHit]:
        _ = (query, query_embedding, pairs, limit)
        raise SQLAlchemyError("external corpus database unavailable")

    def fail_if_lexical_fallback_runs(
        query: str,
        pairs: Sequence[tuple[KnowledgeChunk, KnowledgeSource]],
        *,
        limit: int,
    ) -> list[KnowledgeChunkHit]:
        _ = (query, pairs, limit)
        raise AssertionError("database failures must not be relabeled as lexical fallback")

    monkeypatch.setattr(service, "_active_chunk_pairs", fake_chunk_pairs)
    monkeypatch.setattr(service, "_rank_hits", fail_vector_query)
    monkeypatch.setattr(service, "_rank_lexical_hits", fail_if_lexical_fallback_runs)

    result = service.retrieve("daily readiness training recovery")

    assert result.degraded is True
    assert result.hits == []
    assert result.external_knowledge == []
    assert result.degrade_reason == "SQLAlchemyError"
    assert any("External knowledge retrieval" in item for item in result.uncertainty)


def test_exact_date_briefing_lookup_prefers_external_knowledge_job() -> None:
    ordering = [str(item) for item in _completed_job_ordering(offline_last=False)]

    assert "daily_analysis_job.date DESC" in ordering[0]
    assert "daily_analysis_job.include_external_knowledge DESC" in ordering[1]
    assert "daily_analysis_job.completed_at DESC" in ordering[2]


def test_final_safety_gate_evaluates_suppressed_llm_citation_side_fields() -> None:
    briefing = _minimal_briefing()

    enforced = _enforce_served_briefing_safety(
        SafetyPolicyEngine.from_default_policy(),
        briefing,
        extra_generated_text="Unsafe citation says your diagnosis is overtrained.",
    )

    served_text = json.dumps(enforced.model_dump(mode="json")).lower()
    assert enforced.safety_status.value == "rewritten"
    assert enforced.external_citations == []
    assert "overtrained" not in served_text
    assert "diagnosis is" not in served_text


def test_final_safety_rewrite_preserves_operational_retrieval_uncertainty() -> None:
    briefing = _minimal_briefing().model_copy(
        update={
            "uncertainty": [
                "External knowledge retrieval was unavailable; deterministic briefing was used.",
                "Unsafe uncertainty says your diagnosis is overtrained.",
            ],
        }
    )

    enforced = _enforce_served_briefing_safety(
        SafetyPolicyEngine.from_default_policy(),
        briefing,
    )

    uncertainty = " ".join(enforced.uncertainty)
    served_text = json.dumps(enforced.model_dump(mode="json")).lower()
    assert enforced.safety_status.value == "rewritten"
    assert "External knowledge retrieval" in uncertainty
    assert "Recent-history retrieval" not in uncertainty
    assert "diagnosis is" not in served_text
    assert "overtrained" not in served_text


def test_external_retrieval_uncertainty_preserves_citations_through_safety_passes() -> None:
    hit = KnowledgeChunkHit(
        chunk_id=uuid4(),
        source_id=uuid4(),
        source_version="v1",
        chunk_index=0,
        text=(
            "General research on daily readiness, training recovery, and sleep describes broad "
            "non-personalized patterns for conservative training choices."
        ),
        relevance_score=1.0,
        title="General Training Recovery Reference",
        source="Baseline Test Curation Board",
        url_or_identifier="https://example.org/general-training-recovery",
        trust_level="authoritative",
    )
    retrieval = RetrievalResult(
        observations=[],
        trace_items=[],
        external_hits=[hit],
        external_uncertainty=[
            "External sources are general research context, not personalized advice."
        ],
    )
    citations = _external_citations_from_retrieval([], retrieval)
    base = _minimal_briefing()
    engine = SafetyPolicyEngine.from_default_policy()
    contract = RecommendationContract(
        readiness_state=base.readiness_state,
        recommendation_band=base.recommendation_band,
        confidence=base.confidence,
        personal_evidence=base.evidence,
        memory_observations=[],
        external_citations=citations,
        risk_flags=[],
        recommendation=base.recommendation,
        uncertainty=[*base.uncertainty, *retrieval.external_uncertainty],
        data_quality_notes=[],
        safety_status=base.safety_status,
        safety_note=base.safety_notes[0],
        safety_result={"status": "pending"},
        alternatives=[],
        follow_up=None,
    )
    enforced_contract = engine.enforce_recommendation(contract)

    enforced = _enforce_served_briefing_safety(
        engine,
        base.model_copy(
            update={
                "external_citations": enforced_contract.external_citations,
                "recommendation": enforced_contract.recommendation,
                "uncertainty": enforced_contract.uncertainty,
                "safety_status": enforced_contract.safety_status,
                "safety_notes": [enforced_contract.safety_note],
            }
        ),
    )

    assert [citation.title for citation in enforced.external_citations] == [
        "General Training Recovery Reference"
    ]
    assert "general research context" in " ".join(enforced.uncertainty)


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


class FakeDailyBriefingQueue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.enqueued: list[UUID] = []
        self.error = error

    async def enqueue_daily_briefing(self, *, job_id: UUID) -> str | None:
        if self.error is not None:
            raise self.error
        self.enqueued.append(job_id)
        return f"job:{job_id}"


class _FakeSessionContext:
    def __init__(self, session: Session) -> None:
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *args: object) -> bool:
        return False


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
    briefing_queue: FakeDailyBriefingQueue | None = None,
) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    app.state.briefing_run_inline = run_inline
    if briefing_queue is not None:
        app.state.daily_briefing_queue = briefing_queue
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


def _seed_external_reference(db_session: Session) -> None:
    KnowledgeIngestionPipeline(SQLModelKnowledgeVectorStore(db_session)).ingest(
        KnowledgeSourceDocument(
            title="General Training Recovery Reference",
            author_or_org="Baseline Test Curation Board",
            source_type=KnowledgeSourceType.guideline,
            url_or_identifier="https://example.org/general-training-recovery",
            license_status="CC0-1.0 public domain dedication",
            published_at=dt.date(2024, 1, 1),
            version="v1",
            trust_level=TrustLevel.authoritative,
            content=(
                "General research on daily readiness, training recovery, and sleep describes "
                "broad non-personalized patterns for conservative training choices. This "
                "external source does not describe a Baseline user."
            ),
        )
    )


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
    include_external_knowledge: bool = False,
    force_recompute: bool = False,
) -> dict[str, Any]:
    response = client.post(
        "/v1/analysis/daily",
        json={
            "date": date.isoformat(),
            "force_recompute": force_recompute,
            "include_external_knowledge": include_external_knowledge,
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
    assert "indicator_status" in briefing["goal_tradeoffs"][0]
    assert "evidence_refs" in briefing["goal_tradeoffs"][0]
    assert "missing_data" in briefing["goal_tradeoffs"][0]
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
) -> None:
    _seed_fixture_day(db_session)
    queue = FakeDailyBriefingQueue()
    client = _client(db_session, run_inline=False, briefing_queue=queue)

    job = _generate(client)

    assert job["status"] == "queued"
    assert queue.enqueued == [UUID(job["analysis_job_id"])]
    persisted_job = db_session.get(DailyAnalysisJob, UUID(job["analysis_job_id"]))
    assert persisted_job is not None
    assert persisted_job.status == "queued"


def test_daily_analysis_enqueue_failure_marks_job_failed(db_session: Session) -> None:
    _seed_fixture_day(db_session)
    queue = FakeDailyBriefingQueue(error=RuntimeError("redis down"))
    client = _client(db_session, run_inline=False, briefing_queue=queue)

    response = client.post(
        "/v1/analysis/daily",
        json={
            "date": TARGET_DATE.isoformat(),
            "force_recompute": False,
            "include_external_knowledge": False,
            "privacy_mode": "cloud_assisted",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "analysis_enqueue_failed"
    persisted_job = db_session.exec(select(DailyAnalysisJob)).one()
    assert persisted_job.status == "failed"
    assert persisted_job.error_code == "analysis_enqueue_failed"


@pytest.mark.asyncio
async def test_daily_briefing_worker_runs_persisted_job(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from baseline_api.briefing import worker as briefing_worker

    _seed_fixture_day(db_session)
    job = DailyBriefingService(db_session).create_daily_job(
        DailyAnalysisRequest(
            date=TARGET_DATE,
            force_recompute=False,
            include_external_knowledge=False,
            privacy_mode="cloud_assisted",
        )
    )

    def fake_orchestrator(*, session: Session, router: object) -> FakeLLMExplainer:
        return FakeLLMExplainer(session)

    monkeypatch.setattr(briefing_worker, "get_settings", _settings)
    monkeypatch.setattr(briefing_worker, "build_default_router", lambda settings: object())
    monkeypatch.setattr(briefing_worker, "LLMOrchestrator", fake_orchestrator)

    result = await briefing_worker.daily_briefing(
        {"session_maker": lambda: _FakeSessionContext(db_session)},
        str(job.id),
    )

    assert result["analysis_job_id"] == str(job.id)
    assert result["status"] == "completed"
    persisted_job = db_session.get(DailyAnalysisJob, job.id)
    assert persisted_job is not None
    assert persisted_job.status == "completed"


def test_worker_settings_registers_daily_briefing_without_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://baseline@localhost:5433/baseline",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    import baseline_api.worker as worker_settings
    from baseline_api.config import get_settings

    get_settings.cache_clear()
    worker_settings = importlib.reload(worker_settings)

    daily_briefing_function = next(
        function
        for function in worker_settings.WorkerSettings.functions
        if getattr(function, "name", None) == "daily_briefing"
    )

    assert daily_briefing_function.max_tries == 1


def test_worker_startup_marks_stale_running_daily_briefing_jobs_failed(
    db_session: Session,
) -> None:
    import baseline_api.worker as worker_settings

    _seed_fixture_day(db_session)
    service = DailyBriefingService(db_session)
    request = DailyAnalysisRequest(
        date=TARGET_DATE,
        force_recompute=False,
        include_external_knowledge=False,
        privacy_mode="cloud_assisted",
    )
    stale_job = service.create_daily_job(request)
    fresh_job = service.create_daily_job(request)
    now = dt.datetime(2026, 7, 5, 12, 0, tzinfo=dt.UTC)
    stale_job.status = "running"
    stale_job.started_at = now - dt.timedelta(hours=2)
    fresh_job.status = "running"
    fresh_job.started_at = now - dt.timedelta(minutes=5)
    db_session.add(stale_job)
    db_session.add(fresh_job)
    db_session.commit()

    recovered = worker_settings.mark_stale_running_daily_briefing_jobs_failed(
        {"session_maker": lambda: _FakeSessionContext(db_session)},
        now=now,
    )

    db_session.refresh(stale_job)
    db_session.refresh(fresh_job)
    assert recovered == 1
    assert stale_job.status == "failed"
    assert stale_job.error_code == "analysis_worker_restarted"
    assert fresh_job.status == "running"


def test_llm_failure_degrades_to_deterministic_briefing(db_session: Session) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=FailingLLMExplainer())

    _generate(client)
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert "deterministic" in briefing["recommendation"]["primary"].lower()
    trace = db_session.get(ReasoningTrace, UUID(briefing["trace_id"]))
    assert trace is not None
    generation = trace.trace_payload["briefing_generation"]
    assert generation["status"] == "degraded"
    assert generation["degrade_reason"] == "RuntimeError"
    assert {"stage": "llm_explanation", "reason": "RuntimeError"} in generation["degraded_stages"]


def test_feature_computation_failure_degrades_to_deterministic_briefing(
    db_session: Session,
    monkeypatch: Any,
) -> None:
    _seed_fixture_day(db_session)
    for feature in db_session.exec(select(DerivedDailyFeature)).all():
        db_session.delete(feature)
    db_session.flush()

    def fail_features(self: DailyBriefingService, **_: Any) -> DerivedDailyFeature:
        raise RuntimeError("feature worker down")

    monkeypatch.setattr(DailyBriefingService, "_load_or_compute_features", fail_features)
    client = _client(db_session)

    _generate(client, privacy_mode="local_only")
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert briefing["evidence"]
    assert any(
        note["metric"] == "missing_feature_computation" for note in briefing["data_quality_notes"]
    )
    trace = db_session.exec(select(ReasoningTrace)).one()
    generation = trace.trace_payload["briefing_generation"]
    assert generation["status"] == "degraded"
    assert {"stage": "features", "reason": "RuntimeError"} in generation["degraded_stages"]
    feature_stage = next(stage for stage in generation["stages"] if stage["stage"] == "features")
    assert feature_stage["status"] == "degraded"


def test_sync_freshness_failure_flags_stale_sources_and_serves_briefing(
    db_session: Session,
    monkeypatch: Any,
) -> None:
    _seed_fixture_day(db_session)
    original_exec = db_session.exec
    sync_failed = False

    def flaky_exec(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal sync_failed
        if not sync_failed and "FROM normalized_health_metric" in str(statement):
            sync_failed = True
            raise RuntimeError("sync unavailable")
        return original_exec(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "exec", flaky_exec)
    client = _client(db_session)

    _generate(client, privacy_mode="local_only")
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert sync_failed is True
    assert "sync_unavailable" in briefing["data_freshness"]["stale_sources"]
    trace = db_session.exec(select(ReasoningTrace)).one()
    generation = trace.trace_payload["briefing_generation"]
    assert {"stage": "sync", "reason": "RuntimeError"} in generation["degraded_stages"]
    freshness_stage = next(
        stage for stage in generation["stages"] if stage["stage"] == "data_freshness"
    )
    assert freshness_stage["status"] == "degraded"


def test_sync_freshness_db_error_does_not_poison_briefing_session(
    db_session: Session,
    monkeypatch: Any,
) -> None:
    _seed_fixture_day(db_session)
    original_exec = db_session.exec
    sync_failed = False

    def flaky_exec(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal sync_failed
        if not sync_failed and "FROM normalized_health_metric" in str(statement):
            sync_failed = True
            original_exec(text("SELECT 1 / 0"))
        return original_exec(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "exec", flaky_exec)
    client = _client(db_session)

    _generate(client, privacy_mode="local_only")
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert sync_failed is True
    assert briefing["evidence"]
    assert "sync_unavailable" in briefing["data_freshness"]["stale_sources"]
    trace = db_session.exec(select(ReasoningTrace)).one()
    generation = trace.trace_payload["briefing_generation"]
    sync_stage = next(stage for stage in generation["degraded_stages"] if stage["stage"] == "sync")
    assert sync_stage["reason"]


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
    trace = db_session.get(ReasoningTrace, UUID(briefing["trace_id"]))
    assert trace is not None
    generation = trace.trace_payload["briefing_generation"]
    assert generation["status"] == "degraded"
    assert generation["degrade_reason"] == "privacy_mode_local_only"


def test_external_knowledge_opt_in_adds_bound_non_personal_citations(
    db_session: Session,
) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=FakeLLMExplainer(db_session))

    _generate(client)
    _seed_external_reference(db_session)
    db_session.commit()
    _generate(client, include_external_knowledge=True, force_recompute=True)
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert briefing["evidence"]
    assert briefing["external_citations"]
    assert briefing["external_citations"][0]["title"] == "General Training Recovery Reference"
    assert "General research (non-personalized)" in briefing["external_citations"][0]["cited_claim"]
    assert all(
        "General Training Recovery Reference" not in json.dumps(item)
        for item in briefing["evidence"]
    )
    trace = db_session.get(ReasoningTrace, UUID(briefing["trace_id"]))
    assert trace is not None
    generation = trace.trace_payload["briefing_generation"]
    assert generation["external_source_count"] == 1
    assert generation["external_citation_accuracy"] >= 0.95


def test_external_embedding_failure_preserves_degraded_lexical_citations(
    db_session: Session,
    monkeypatch: Any,
) -> None:
    class FailingEmbedder:
        def embed(self, text: str) -> list[float]:
            _ = text
            raise TimeoutError("embedding timeout")

    _seed_fixture_day(db_session)
    _seed_external_reference(db_session)
    db_session.commit()
    monkeypatch.setattr(
        "baseline_api.briefing.service.create_embedder",
        lambda settings=None: FailingEmbedder(),
    )
    client = _client(db_session, llm_explainer=FakeLLMExplainer(db_session))

    _generate(client, include_external_knowledge=True)
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert briefing["external_citations"]
    assert any("lexical corpus retrieval was used" in item for item in briefing["uncertainty"])
    trace = db_session.exec(select(ReasoningTrace)).one()
    generation = trace.trace_payload["briefing_generation"]
    assert generation["retrieval_degraded"] is True
    assert generation["external_source_count"] == 1


@pytest.mark.asyncio
async def test_external_knowledge_without_consent_does_not_create_embedder(
    db_session: Session,
    monkeypatch: Any,
) -> None:
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
            cloud_processing_enabled=False,
            external_llm_enabled=False,
            raw_note_processing_enabled=False,
            timestamp=dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        )
    )
    db_session.commit()

    def fail_create_embedder(settings: Settings | None = None) -> object:
        _ = settings
        raise AssertionError("embedder should not be created without consent")

    monkeypatch.setattr(
        "baseline_api.briefing.service.create_embedder",
        fail_create_embedder,
    )

    result = await DailyBriefingService(db_session)._retrieve_external_knowledge(
        user_id=user.id,
        include_external_knowledge=True,
        privacy_mode=PrivacyMode.cloud_assisted,
        active_goals=[{"category": "vo2_max"}],
    )

    assert result.hits == []
    assert result.external_knowledge == []
    assert result.uncertainty == ["External knowledge was requested but consent is not active."]


def test_external_knowledge_query_includes_goal_topics_only() -> None:
    query = build_external_knowledge_query(
        active_goals=[
            {"category": "strength"},
            {"category": "cognitive_performance"},
        ],
        requested_scope="daily briefing",
    )

    assert "strength training" in query
    assert "cognitive readiness" in query
    assert "daily briefing" in query
    assert "non personalized" in query
    assert "high_sleep_debt" not in query
    assert "mixed" not in query
    assert "HRV is favorable" not in query


def test_external_retrieval_db_error_degrades_without_wrong_history_label(
    db_session: Session,
    monkeypatch: Any,
) -> None:
    _seed_fixture_day(db_session)
    _seed_external_reference(db_session)
    db_session.flush()
    retrieval_failed = False

    def fail_vector_query(
        service: KnowledgeRetrievalService,
        query: str,
        query_embedding: Sequence[float],
        pairs: Sequence[tuple[KnowledgeChunk, KnowledgeSource]],
        *,
        limit: int,
    ) -> list[KnowledgeChunkHit]:
        nonlocal retrieval_failed
        _ = (service, query, query_embedding, pairs, limit)
        retrieval_failed = True
        raise SQLAlchemyError("external corpus database unavailable")

    monkeypatch.setattr(KnowledgeRetrievalService, "_rank_hits", fail_vector_query)
    client = _client(db_session)

    _generate(client, include_external_knowledge=True)
    briefing = client.get(f"/v1/briefings/{TARGET_DATE.isoformat()}").json()["data"]

    assert retrieval_failed is True
    assert briefing["evidence"]
    assert any("External knowledge retrieval" in item for item in briefing["uncertainty"])
    assert not any("Recent-history retrieval" in item for item in briefing["uncertainty"])
    trace = db_session.exec(select(ReasoningTrace)).one()
    generation = trace.trace_payload["briefing_generation"]
    assert generation["retrieval_degraded"] is True
    assert generation["retrieval_degrade_reason"]


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


def test_reenqueue_completed_daily_briefing_returns_existing_result(
    db_session: Session,
) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=FakeLLMExplainer(db_session))

    first = _generate(client)
    recommendation_count = len(
        db_session.exec(select(Recommendation).where(Recommendation.date == TARGET_DATE)).all()
    )

    second = _generate(client)

    assert second["analysis_job_id"] == first["analysis_job_id"]
    assert second["status"] == "completed"
    assert (
        len(db_session.exec(select(Recommendation).where(Recommendation.date == TARGET_DATE)).all())
        == recommendation_count
    )


def test_force_recompute_creates_new_daily_briefing_run(db_session: Session) -> None:
    _seed_fixture_day(db_session)
    client = _client(db_session, llm_explainer=FakeLLMExplainer(db_session))

    first = _generate(client)
    second = _generate(client, force_recompute=True)

    assert second["analysis_job_id"] != first["analysis_job_id"]
    assert second["status"] == "completed"
    jobs = db_session.exec(
        select(DailyAnalysisJob).where(DailyAnalysisJob.date == TARGET_DATE)
    ).all()
    assert len(jobs) == 2
    assert {job.status for job in jobs} == {"completed"}


@pytest.mark.asyncio
async def test_failed_daily_briefing_job_retries_up_to_max_retries(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed jobs retry on the same row via API/cron-like get-or-create paths."""
    user = _seed_fixture_day(db_session)
    service = DailyBriefingService(
        db_session,
        llm_explainer=FakeLLMExplainer(db_session),
        settings=_settings(),
    )
    job = service.create_daily_job(
        DailyAnalysisRequest(
            date=TARGET_DATE,
            force_recompute=False,
            include_external_knowledge=False,
            privacy_mode="cloud_assisted",
        ),
        user=user,
    )
    job.status = "failed"
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        DailyBriefingService,
        "_load_or_compute_features_with_degraded_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("features down")),
    )

    max_retries = _settings().daily_briefing_max_retries
    for expected_retry in range(1, max_retries + 1):
        # The API/cron path resolves to the existing failed job row.
        resolved = service.get_or_create_daily_job_for_date(TARGET_DATE, user=user)
        assert resolved.id == job.id

        with pytest.raises(BriefingError):
            await service.run_daily_job(job.id)
        db_session.refresh(job)
        assert job.retry_count == expected_retry
        assert job.status == "failed"

    # Once retries are exhausted, run_daily_job refuses another attempt.
    resolved = service.get_or_create_daily_job_for_date(TARGET_DATE, user=user)
    assert resolved.id == job.id
    with pytest.raises(BriefingError) as exc_info:
        await service.run_daily_job(job.id)
    assert exc_info.value.code == "analysis_job_max_retries_exceeded"
    db_session.refresh(job)
    assert job.retry_count == max_retries
    assert job.status == "failed"


def test_get_or_create_daily_job_returns_exhausted_failed_job_without_new_run(
    db_session: Session,
) -> None:
    """An exhausted failed job is returned unchanged; no new job is created."""
    user = _seed_fixture_day(db_session)
    service = DailyBriefingService(db_session, settings=_settings())
    job = service.create_daily_job(
        DailyAnalysisRequest(
            date=TARGET_DATE,
            force_recompute=False,
            include_external_knowledge=False,
            privacy_mode="cloud_assisted",
        ),
        user=user,
    )
    job.status = "failed"
    job.retry_count = _settings().daily_briefing_max_retries
    db_session.add(job)
    db_session.commit()

    resolved = service.get_or_create_daily_job_for_date(TARGET_DATE, user=user)

    assert resolved.id == job.id
    assert resolved.status == "failed"
    assert resolved.retry_count == _settings().daily_briefing_max_retries
    assert db_session.exec(select(DailyAnalysisJob)).one().id == job.id


@pytest.mark.asyncio
async def test_daily_briefing_worker_propagates_briefing_error_on_failure(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The arq worker must surface briefing failures so arq marks the job failed."""
    from baseline_api.briefing import worker as briefing_worker

    _seed_fixture_day(db_session)
    job = DailyBriefingService(db_session).create_daily_job(
        DailyAnalysisRequest(
            date=TARGET_DATE,
            force_recompute=False,
            include_external_knowledge=False,
            privacy_mode="cloud_assisted",
        ),
        user=db_session.exec(select(User)).first(),
    )
    job.status = "failed"
    job.retry_count = _settings().daily_briefing_max_retries
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(briefing_worker, "get_settings", _settings)
    monkeypatch.setattr(briefing_worker, "build_default_router", lambda settings: object())

    async def failing_run_daily_job(
        self: DailyBriefingService,
        job_id: UUID,
    ) -> DailyAnalysisResponse:
        raise BriefingError(
            code="daily_briefing_generation_failed",
            message="Daily briefing generation failed.",
            status_code=502,
        )

    monkeypatch.setattr(DailyBriefingService, "run_daily_job", failing_run_daily_job)

    with pytest.raises(BriefingError):
        await briefing_worker.daily_briefing(
            {"session_maker": lambda: _FakeSessionContext(db_session)},
            str(job.id),
        )


@pytest.mark.asyncio
async def test_failed_daily_briefing_retry_clears_stale_error_fields(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _seed_fixture_day(db_session)
    service = DailyBriefingService(
        db_session,
        llm_explainer=FakeLLMExplainer(db_session),
        settings=_settings(),
    )
    job = service.create_daily_job(
        DailyAnalysisRequest(
            date=TARGET_DATE,
            force_recompute=False,
            include_external_knowledge=False,
            privacy_mode="cloud_assisted",
        ),
        user=user,
    )
    job.status = "failed"
    job.error_code = "previous_error"
    job.error_message = "previous failure"
    job.completed_at = dt.datetime(2026, 7, 4, 5, 0, tzinfo=dt.UTC)
    db_session.add(job)
    db_session.commit()

    # Patch feature load to succeed so the retry completes cleanly.
    monkeypatch.setattr(
        DailyBriefingService,
        "_load_or_compute_features_with_degraded_mode",
        lambda self, *, user_id, target_date, force_recompute: (
            db_session.exec(
                select(DerivedDailyFeature).where(
                    DerivedDailyFeature.user_id == user_id,
                    DerivedDailyFeature.date == target_date,
                )
            ).one(),
            None,
        ),
    )

    response = await service.run_daily_job(job.id)
    db_session.refresh(job)
    assert response.status == AnalysisJobStatus.completed
    assert job.retry_count == 1
    assert job.error_code is None
    assert job.error_message is None
    assert job.completed_at is not None


def test_worker_settings_cron_schedule_includes_daily_and_memory_jobs() -> None:
    cron_names = {job.name.replace("cron:", "") for job in WorkerSettings.cron_jobs}

    assert "daily_briefing_cron" in cron_names
    assert "compact_weekly_memory" in cron_names
    assert "compact_monthly_memory" in cron_names
    assert "compact_quarterly_memory" in cron_names


@pytest.mark.asyncio
async def test_stale_briefing_alert_fires_when_no_briefing_by_alert_hour(
    db_session: Session,
) -> None:
    user = _seed_fixture_day(db_session)
    settings = _settings()
    alert_hour = settings.stale_briefing_alert_hour_utc
    before_alert = dt.datetime(2026, 7, 4, alert_hour - 1, 0, tzinfo=dt.UTC)
    after_alert = dt.datetime(2026, 7, 4, alert_hour, 0, tzinfo=dt.UTC)

    assert stale_briefing_alert(db_session, settings=settings, now=before_alert) == []

    alerts = stale_briefing_alert(db_session, settings=settings, now=after_alert)
    assert len(alerts) == 1
    assert alerts[0].alert_type == "stale_briefing"
    assert alerts[0].metadata["date"] == "2026-07-04"

    # Completing a briefing for the date clears the alert.
    service = DailyBriefingService(db_session, llm_explainer=FakeLLMExplainer(db_session))
    job = service.get_or_create_daily_job_for_date(TARGET_DATE, user=user)
    db_session.commit()
    await service.run_daily_job(job.id)
    assert stale_briefing_alert(db_session, settings=settings, now=after_alert) == []
