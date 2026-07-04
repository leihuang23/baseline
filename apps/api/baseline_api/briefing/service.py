"""Daily briefing assembly and persistence."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlmodel import Session, col, select

from baseline_api.db.models.assessment import DailyAnalysisJob, ReasoningTrace, Recommendation
from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.enums import (
    RecommendationType as ModelRecommendationType,
)
from baseline_api.db.models.enums import (
    SafetyStatus as ModelSafetyStatus,
)
from baseline_api.db.models.features import DerivedDailyFeature
from baseline_api.db.models.ingestion import NormalizedHealthMetric
from baseline_api.db.models.sessions import SleepSession, WorkoutSession
from baseline_api.db.models.user import User
from baseline_api.db.repositories.assessment import (
    ReadinessAssessmentRepository,
    RecommendationRepository,
)
from baseline_api.features.assembler import assemble_daily_features
from baseline_api.features.worker import (
    _load_cardio_samples,
    _load_sleep_sessions,
    _load_vo2_samples,
    _load_workouts,
    _upsert_derived_daily_feature,
)
from baseline_api.goals import GoalService
from baseline_api.llm.orchestrator import OrchestratorResult
from baseline_api.llm.schemas import LLMExplanationOutput, PromptInputs, TaskType
from baseline_api.llm.validation import degraded_output
from baseline_api.observability.metrics import (
    add_llm_cost,
    increment_llm_generation_result,
    observe_briefing_latency,
)
from baseline_api.observability.tracing import create_job_context, use_trace_context
from baseline_api.reasoning.service import ReasoningService, features_to_mapping
from baseline_api.safety.engine import SafetyPolicyEngine
from baseline_api.schemas.api import (
    CandidateOption,
    DailyAnalysisRequest,
    DailyAnalysisResponse,
    DailyBriefingResponse,
    DataFreshness,
    GoalTradeoff,
)
from baseline_api.schemas.enums import (
    AnalysisJobStatus,
    DataQualitySeverity,
    PrivacyMode,
    SafetyStatus,
)
from baseline_api.schemas.recommendation import (
    DataQualityNote,
    FollowUpPrompt,
    MemoryObservation,
    PersonalEvidence,
    RecommendationAlternative,
    RecommendationContract,
    RecommendationSummary,
)


class LLMExplainer(Protocol):
    async def explain(
        self,
        *,
        user_id: UUID,
        prompt_inputs: PromptInputs,
        run_type: Any,
    ) -> OrchestratorResult:
        """Generate or degrade a bounded explanation."""


@dataclass(frozen=True)
class BriefingError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class RetrievalResult:
    observations: list[MemoryObservation]
    trace_items: list[dict[str, Any]]
    degraded: bool = False
    degrade_reason: str | None = None


class DailyBriefingService:
    """Orchestrate features, reasoning, explanation, safety, and persistence."""

    def __init__(
        self,
        session: Session,
        *,
        llm_explainer: LLMExplainer | None = None,
        safety_engine: SafetyPolicyEngine | None = None,
    ) -> None:
        self._session = session
        self._llm_explainer = llm_explainer
        self._safety_engine = safety_engine or SafetyPolicyEngine.from_default_policy()
        self._assessments = ReadinessAssessmentRepository(session)
        self._recommendations = RecommendationRepository(session)

    async def generate_daily(self, request: DailyAnalysisRequest) -> DailyAnalysisResponse:
        """Create and run a daily analysis job inline.

        The API route uses ``create_daily_job`` plus a background task by default. This
        inline path keeps tests and local scripted runs deterministic while exercising
        the same persisted job record and pipeline implementation.
        """

        job = self.create_daily_job(request)
        return await self.run_daily_job(job.id)

    def create_daily_job(self, request: DailyAnalysisRequest) -> DailyAnalysisJob:
        user = self._get_single_user()
        job_id = uuid4()
        context = create_job_context(job_id=str(job_id), internal_user_id=str(user.id))
        job = DailyAnalysisJob(
            id=job_id,
            user_id=user.id,
            date=request.date,
            status=AnalysisJobStatus.queued.value,
            force_recompute=request.force_recompute,
            include_external_knowledge=request.include_external_knowledge,
            privacy_mode=request.privacy_mode.value,
            request_trace_id=context.trace_id,
            stage_trace=[
                {
                    "stage": "enqueue",
                    "status": "success",
                    "trace_id": context.trace_id,
                    "job_id": str(job_id),
                    "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
                }
            ],
        )
        self._session.add(job)
        self._session.commit()
        return job

    async def run_daily_job(self, job_id: UUID) -> DailyAnalysisResponse:
        job = self._session.get(DailyAnalysisJob, job_id)
        if job is None:
            raise BriefingError(
                code="analysis_job_not_found",
                message="Daily analysis job not found.",
                status_code=404,
            )
        user = self._session.get(User, job.user_id)
        if user is None:
            raise BriefingError(
                code="user_not_initialized",
                message="No Baseline user is available for briefing generation.",
                status_code=409,
            )
        request = DailyAnalysisRequest(
            date=job.date,
            force_recompute=job.force_recompute,
            include_external_knowledge=job.include_external_knowledge,
            privacy_mode=PrivacyMode(job.privacy_mode),
        )
        context = create_job_context(
            job_id=str(job.id),
            trace_id=job.request_trace_id,
            internal_user_id=str(user.id),
        )
        started = time.perf_counter()
        job.status = AnalysisJobStatus.running.value
        job.started_at = dt.datetime.now(dt.UTC)
        job.stage_trace = [
            *job.stage_trace,
            _stage_event("job_running", trace_id=context.trace_id, job_id=job.id),
        ]
        self._session.add(job)
        self._session.commit()

        with use_trace_context(context):
            try:
                feature = self._load_or_compute_features(
                    user_id=user.id,
                    target_date=request.date,
                    force_recompute=request.force_recompute,
                )
                checkin = self._load_checkin(user.id, request.date)
                freshness = _data_freshness(self._session, feature, checkin)
                stage_trace = [
                    *job.stage_trace,
                    _stage_event(
                        "features",
                        trace_id=context.trace_id,
                        job_id=job.id,
                        derived_daily_feature_id=str(feature.id),
                        feature_version=feature.feature_version,
                    ),
                    _stage_event(
                        "data_freshness",
                        trace_id=context.trace_id,
                        job_id=job.id,
                        data_freshness=freshness.model_dump(mode="json"),
                    ),
                ]
                active_goals = self._active_goals()
                assessment = ReasoningService(self._session).assess_and_persist(
                    user_id=user.id,
                    derived_features=feature,
                    active_goals=active_goals,
                    recent_memory=[],
                    daily_check_in=_checkin_mapping(checkin),
                    include_external_knowledge=request.include_external_knowledge,
                )
                briefing_trace_id = str(assessment.reasoning_trace_id)
                stage_trace = _retarget_stage_trace(stage_trace, trace_id=briefing_trace_id)
                stage_trace.append(
                    _stage_event(
                        "reasoning",
                        trace_id=briefing_trace_id,
                        job_id=job.id,
                        reasoning_trace_id=str(assessment.reasoning_trace_id),
                        readiness_state=assessment.readiness_state.value,
                        recommendation_band=assessment.recommendation_band.value,
                    )
                )
                retrieval = self._retrieve_recent_history(user.id, request.date)
                stage_trace.append(
                    _stage_event(
                        "retrieval",
                        trace_id=briefing_trace_id,
                        job_id=job.id,
                        status="degraded" if retrieval.degraded else "success",
                        degraded=retrieval.degraded,
                        degrade_reason=retrieval.degrade_reason,
                        observation_count=len(retrieval.observations),
                    )
                )
                prompt_inputs = PromptInputs(
                    task_type=TaskType.simple_explanation,
                    request_text="Generate today's Baseline daily briefing.",
                    deterministic_assessment=_assessment_mapping(assessment),
                    derived_features=features_to_mapping(feature),
                    retrieved_evidence=retrieval.trace_items,
                    external_knowledge=[],
                    raw_samples=[],
                    raw_notes=[],
                )
                explanation = await self._explain(
                    user_id=user.id,
                    prompt_inputs=prompt_inputs,
                    privacy_mode=request.privacy_mode,
                )
                stage_trace.append(
                    _stage_event(
                        "llm_explanation",
                        trace_id=briefing_trace_id,
                        job_id=job.id,
                        status="degraded" if explanation.degraded else "success",
                        degraded=explanation.degraded,
                        degrade_reason=explanation.degrade_reason,
                        model_run_ids=[
                            str(row.id) for row in explanation.model_runs if hasattr(row, "id")
                        ],
                        total_cost=_total_cost(explanation),
                    )
                )
                briefing = self._assemble_briefing(
                    target_date=request.date,
                    assessment=_assessment_mapping(assessment),
                    feature=feature,
                    checkin=checkin,
                    freshness=freshness,
                    retrieval=retrieval,
                    explanation=explanation.output,
                )
                stage_trace.append(
                    _stage_event(
                        "safety",
                        trace_id=briefing_trace_id,
                        job_id=job.id,
                        status=briefing.safety_status.value,
                        safety_notes=briefing.safety_notes,
                    )
                )
                recommendation = self._persist_recommendation(
                    user_id=user.id,
                    target_date=request.date,
                    briefing=briefing,
                    assessment=_assessment_mapping(assessment),
                    explanation=explanation,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                stage_trace.append(
                    _stage_event(
                        "persistence",
                        trace_id=briefing_trace_id,
                        job_id=job.id,
                        recommendation_id=str(recommendation.id),
                        reasoning_trace_id=str(assessment.reasoning_trace_id),
                    )
                )
                self._record_trace(
                    trace_id=assessment.reasoning_trace_id,
                    job_id=job.id,
                    latency_ms=latency_ms,
                    recommendation=recommendation,
                    retrieval=retrieval,
                    explanation=explanation,
                    stage_trace=stage_trace,
                )
                job.status = AnalysisJobStatus.completed.value
                job.reasoning_trace_id = assessment.reasoning_trace_id
                job.recommendation_id = recommendation.id
                job.stage_trace = stage_trace
                job.completed_at = dt.datetime.now(dt.UTC)
                self._session.add(job)
                self._session.commit()
                observe_briefing_latency(latency_ms / 1000)
                increment_llm_generation_result(
                    status="degraded" if explanation.degraded else "success"
                )
                _record_model_cost(explanation)
                return DailyAnalysisResponse(
                    analysis_job_id=job.id,
                    status=AnalysisJobStatus.completed,
                    estimated_completion_seconds=0,
                )
            except BriefingError:
                self._mark_job_failed(job.id, error_code="briefing_error", error_message=None)
                increment_llm_generation_result(status="failed")
                raise
            except Exception as exc:
                self._session.rollback()
                self._mark_job_failed(
                    job.id,
                    error_code=type(exc).__name__,
                    error_message="Daily briefing generation failed.",
                )
                increment_llm_generation_result(status="failed")
                raise BriefingError(
                    code="briefing_generation_failed",
                    message="Daily briefing generation failed.",
                    status_code=500,
                    details={"error_type": type(exc).__name__},
                ) from exc

    def get_briefing(
        self,
        *,
        target_date: dt.date,
        offline_last: bool = False,
    ) -> DailyBriefingResponse:
        user = self._get_single_user()
        recommendation = self._recommendations.latest_for_user_date(
            user_id=user.id,
            date=target_date,
        )
        if recommendation is None and offline_last:
            recommendation = self._recommendations.latest_for_user_on_or_before(
                user_id=user.id,
                date=target_date,
            )
        if recommendation is None:
            raise BriefingError(
                code="briefing_not_found",
                message="Daily briefing not found.",
                status_code=404,
            )
        if not recommendation.briefing_payload:
            raise BriefingError(
                code="briefing_payload_missing",
                message="Stored recommendation does not include a briefing payload.",
                status_code=409,
            )
        return DailyBriefingResponse.model_validate(recommendation.briefing_payload)

    def _load_or_compute_features(
        self,
        *,
        user_id: UUID,
        target_date: dt.date,
        force_recompute: bool,
    ) -> DerivedDailyFeature:
        if not force_recompute:
            existing = self._session.exec(
                select(DerivedDailyFeature).where(
                    DerivedDailyFeature.user_id == user_id,
                    DerivedDailyFeature.date == target_date,
                )
            ).first()
            if existing is not None:
                return existing

        bundle = assemble_daily_features(
            target_date,
            sleep_sessions=_load_sleep_sessions(self._session, user_id, target_date),
            hrv_samples=_load_cardio_samples(
                self._session,
                user_id,
                target_date,
                metric_type=_metric_type("heart_rate_variability"),
            ),
            rhr_samples=_load_cardio_samples(
                self._session,
                user_id,
                target_date,
                metric_type=_metric_type("resting_heart_rate"),
            ),
            workouts=_load_workouts(self._session, user_id, target_date),
            vo2_samples=_load_vo2_samples(self._session, user_id, target_date),
            personal_sleep_need_hours=8.0,
            computed_at=dt.datetime.now(dt.UTC),
        )
        return _upsert_derived_daily_feature(
            self._session,
            user_id,
            target_date,
            bundle.to_derived_daily_feature_fields(),
        )

    def _load_checkin(self, user_id: UUID, target_date: dt.date) -> DailyCheckIn | None:
        return self._session.exec(
            select(DailyCheckIn)
            .where(DailyCheckIn.user_id == user_id, DailyCheckIn.date == target_date)
            .order_by(col(DailyCheckIn.created_at).desc())
        ).first()

    def _active_goals(self) -> list[dict[str, Any]]:
        goal_set = GoalService(self._session).get_active_goal_set()
        return [goal.model_dump(mode="json") for goal in goal_set.goals]

    def _retrieve_recent_history(self, user_id: UUID, target_date: dt.date) -> RetrievalResult:
        try:
            rows = self._session.exec(
                select(Recommendation)
                .where(
                    Recommendation.user_id == user_id,
                    Recommendation.date < target_date,
                )
                .order_by(col(Recommendation.date).desc(), col(Recommendation.created_at).desc())
                .limit(7)
            ).all()
        except Exception as exc:
            self._session.rollback()
            return RetrievalResult(
                observations=[],
                trace_items=[],
                degraded=True,
                degrade_reason=type(exc).__name__,
            )

        observations = [
            MemoryObservation(
                observation=f"{row.date.isoformat()}: {row.recommendation_text}",
                relevance="Recent prior briefing context for continuity.",
                period=row.date.isoformat(),
            )
            for row in rows
        ]
        return RetrievalResult(
            observations=observations,
            trace_items=[
                {
                    "date": row.date.isoformat(),
                    "recommendation_id": str(row.id),
                    "recommendation_type": row.recommendation_type.value,
                }
                for row in rows
            ],
        )

    async def _explain(
        self,
        *,
        user_id: UUID,
        prompt_inputs: PromptInputs,
        privacy_mode: PrivacyMode,
    ) -> OrchestratorResult:
        if privacy_mode == PrivacyMode.local_only:
            return OrchestratorResult(
                output=degraded_output(
                    deterministic_assessment=prompt_inputs.deterministic_assessment,
                    reason="privacy_mode_local_only",
                ),
                degraded=True,
                degrade_reason="privacy_mode_local_only",
            )
        if self._llm_explainer is None:
            return OrchestratorResult(
                output=degraded_output(
                    deterministic_assessment=prompt_inputs.deterministic_assessment,
                    reason="llm_not_configured",
                ),
                degraded=True,
                degrade_reason="llm_not_configured",
            )
        try:
            return await self._llm_explainer.explain(
                user_id=user_id,
                prompt_inputs=prompt_inputs,
                run_type=_run_type("daily_briefing"),
            )
        except Exception as exc:
            return OrchestratorResult(
                output=degraded_output(
                    deterministic_assessment=prompt_inputs.deterministic_assessment,
                    reason=type(exc).__name__,
                ),
                degraded=True,
                degrade_reason=type(exc).__name__,
            )

    def _assemble_briefing(
        self,
        *,
        target_date: dt.date,
        assessment: dict[str, Any],
        feature: DerivedDailyFeature,
        checkin: DailyCheckIn | None,
        freshness: DataFreshness,
        retrieval: RetrievalResult,
        explanation: LLMExplanationOutput,
    ) -> DailyBriefingResponse:
        contract = RecommendationContract(
            readiness_state=assessment["readiness_state"],
            recommendation_band=assessment["recommendation_band"],
            confidence=assessment["confidence"],
            personal_evidence=_personal_evidence(assessment["evidence_items"]),
            memory_observations=retrieval.observations,
            external_citations=explanation.external_citations,
            risk_flags=assessment["risk_flags"],
            recommendation=RecommendationSummary(
                primary=_primary_recommendation(assessment, explanation),
                avoid=_avoidance_note(assessment),
            ),
            uncertainty=_uncertainty(assessment, explanation, retrieval),
            data_quality_notes=_data_quality_notes(feature),
            safety_status=SafetyStatus.passed,
            safety_note="This is wellness decision support, not medical advice.",
            safety_result={"status": "pending"},
            alternatives=_alternatives(assessment["candidate_options"]),
            follow_up=_follow_up(assessment),
        )
        enforced = self._safety_engine.enforce_recommendation(contract)
        safety_notes = [enforced.safety_note]
        candidate_options = _candidate_options(assessment["candidate_options"])
        goal_tradeoffs = _goal_tradeoffs(assessment["goal_tradeoffs"])
        briefing = DailyBriefingResponse(
            date=target_date,
            readiness_state=enforced.readiness_state,
            confidence=enforced.confidence,
            data_freshness=freshness,
            evidence=enforced.personal_evidence,
            memory_observations=enforced.memory_observations,
            external_citations=enforced.external_citations,
            risk_flags=enforced.risk_flags,
            recommendation=enforced.recommendation,
            recommendation_band=enforced.recommendation_band,
            candidate_options=candidate_options,
            goal_tradeoffs=goal_tradeoffs,
            uncertainty=enforced.uncertainty,
            data_quality_notes=enforced.data_quality_notes,
            what_would_change_my_mind=_what_would_change_my_mind(enforced.uncertainty),
            alternatives=enforced.alternatives,
            follow_up=enforced.follow_up,
            safety_status=enforced.safety_status,
            safety_notes=safety_notes,
            trace_id=assessment["reasoning_trace_id"],
            generated_at=dt.datetime.now(dt.UTC),
        )
        return _enforce_served_briefing_safety(self._safety_engine, briefing)

    def _persist_recommendation(
        self,
        *,
        user_id: UUID,
        target_date: dt.date,
        briefing: DailyBriefingResponse,
        assessment: dict[str, Any],
        explanation: OrchestratorResult,
    ) -> Recommendation:
        model_run_id = _first_model_run_id(explanation)
        recommendation = Recommendation(
            user_id=user_id,
            date=target_date,
            recommendation_type=_recommendation_type(briefing.recommendation_band.value),
            recommendation_text=briefing.recommendation.primary,
            candidate_options=[item.model_dump(mode="json") for item in briefing.candidate_options],
            evidence_refs=[item.model_dump(mode="json") for item in briefing.evidence],
            safety_status=ModelSafetyStatus(briefing.safety_status.value),
            safety_result={
                "notes": briefing.safety_notes,
                "status": briefing.safety_status.value,
                "trace_id": str(briefing.trace_id),
                "reasoning_risk_flags": assessment["risk_flags"],
            },
            model_run_id=model_run_id,
            reasoning_trace_id=briefing.trace_id,
            briefing_payload=briefing.model_dump(mode="json"),
        )
        return self._recommendations.create(recommendation)

    def _record_trace(
        self,
        *,
        trace_id: UUID,
        job_id: UUID,
        latency_ms: int,
        recommendation: Recommendation,
        retrieval: RetrievalResult,
        explanation: OrchestratorResult,
        stage_trace: list[dict[str, Any]],
    ) -> None:
        trace = self._session.get(ReasoningTrace, trace_id)
        if trace is None:
            return
        payload = dict(trace.trace_payload)
        payload["briefing_generation"] = {
            "job_id": str(job_id),
            "recommendation_id": str(recommendation.id),
            "status": "degraded" if explanation.degraded else "success",
            "degrade_reason": explanation.degrade_reason,
            "retrieval_degraded": retrieval.degraded,
            "retrieval_degrade_reason": retrieval.degrade_reason,
            "model_run_ids": [str(row.id) for row in explanation.model_runs if hasattr(row, "id")],
            "total_cost": _total_cost(explanation),
            "latency_ms": latency_ms,
            "p95_target_ms": 300_000,
            "within_p95_target": latency_ms < 300_000,
            "stages": stage_trace,
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        trace.trace_payload = payload
        self._session.add(trace)

    def _mark_job_failed(
        self,
        job_id: UUID,
        *,
        error_code: str,
        error_message: str | None,
    ) -> None:
        job = self._session.get(DailyAnalysisJob, job_id)
        if job is None:
            return
        job.status = AnalysisJobStatus.failed.value
        job.error_code = error_code
        job.error_message = error_message
        job.completed_at = dt.datetime.now(dt.UTC)
        job.stage_trace = [
            *job.stage_trace,
            _stage_event("job_failed", trace_id=job.request_trace_id, job_id=job.id),
        ]
        self._session.add(job)
        self._session.commit()

    def _get_single_user(self) -> User:
        users = list(self._session.exec(select(User).order_by(col(User.created_at)).limit(2)).all())
        if not users:
            raise BriefingError(
                code="user_not_initialized",
                message="No Baseline user is available for briefing generation.",
                status_code=409,
            )
        if len(users) > 1:
            raise BriefingError(
                code="ambiguous_user",
                message="Briefing generation requires an authenticated user context.",
                status_code=409,
            )
        return users[0]


def _assessment_mapping(assessment: Any) -> dict[str, Any]:
    return {
        "assessment_version": assessment.assessment_version,
        "readiness_state": assessment.readiness_state,
        "evidence_items": assessment.evidence_items,
        "risk_flags": assessment.risk_flags,
        "recommendation_band": assessment.recommendation_band,
        "confidence": assessment.confidence,
        "uncertainty": assessment.uncertainty,
        "follow_up_questions": assessment.follow_up_questions,
        "goal_tradeoffs": assessment.goal_tradeoffs,
        "candidate_options": assessment.candidate_options,
        "hard_safety_flags": assessment.hard_safety_flags,
        "reasoning_trace_id": assessment.reasoning_trace_id,
    }


def _checkin_mapping(checkin: DailyCheckIn | None) -> dict[str, Any] | None:
    if checkin is None:
        return None
    return {
        "energy_score": checkin.energy_score,
        "mood_score": checkin.mood_score,
        "soreness_score": checkin.soreness_score,
        "stress_score": checkin.stress_score,
        "perceived_recovery_score": checkin.perceived_recovery_score,
        "food_quality_score": checkin.food_quality_score,
        "alcohol_flag": checkin.alcohol_flag,
        "illness_flag": checkin.illness_flag,
        "injury_flag": checkin.injury_flag,
        "travel_flag": checkin.travel_flag,
        "structured_notes": checkin.structured_notes,
    }


def _stage_event(
    stage: str,
    *,
    trace_id: str,
    job_id: UUID,
    status: str = "success",
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": status,
        "trace_id": trace_id,
        "job_id": str(job_id),
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
        **{key: value for key, value in metadata.items() if value is not None},
    }


def _retarget_stage_trace(
    stage_trace: Sequence[Mapping[str, Any]],
    *,
    trace_id: str,
) -> list[dict[str, Any]]:
    return [{**dict(stage), "trace_id": trace_id} for stage in stage_trace]


def _enforce_served_briefing_safety(
    safety_engine: SafetyPolicyEngine,
    briefing: DailyBriefingResponse,
) -> DailyBriefingResponse:
    result = safety_engine.evaluate(
        request_text=" ".join(briefing.risk_flags),
        generated_text=_served_briefing_text(briefing),
    )
    safety_notes = [result.safety_note]
    if result.status is SafetyStatus.passed:
        return briefing.model_copy(
            update={
                "safety_status": result.status,
                "safety_notes": safety_notes,
            }
        )

    return briefing.model_copy(
        update={
            "evidence": [
                PersonalEvidence(
                    metric="safety_review",
                    value="applied",
                    interpretation="The post-generation safety policy filtered unsafe wording.",
                    source="safety_policy",
                )
            ],
            "memory_observations": [],
            "external_citations": [],
            "risk_flags": list(briefing.risk_flags),
            "recommendation": RecommendationSummary(primary=result.safe_output, avoid=None),
            "candidate_options": [
                CandidateOption(
                    label="Safety-filtered option",
                    recommendation_band=briefing.recommendation_band,
                    rationale=(
                        "Use the lower-risk wellness framing while unsafe wording is removed."
                    ),
                )
            ],
            "goal_tradeoffs": [
                GoalTradeoff(
                    goal="Training goal",
                    tradeoff=(
                        "Prioritize lower-risk wellness choices until the uncertainty is clearer."
                    ),
                )
            ],
            "uncertainty": [
                "Baseline can discuss wellness signals, not medical conclusions.",
            ],
            "data_quality_notes": [
                DataQualityNote(
                    metric="safety_review",
                    note="Unsafe medical-certainty language was removed before persistence.",
                    severity=DataQualitySeverity.info,
                )
            ],
            "what_would_change_my_mind": [
                "A safer, non-medical explanation grounded in the deterministic signals.",
            ],
            "alternatives": [],
            "follow_up": None,
            "safety_status": result.status,
            "safety_notes": safety_notes,
        }
    )


def _served_briefing_text(briefing: DailyBriefingResponse) -> str:
    parts: list[str] = [
        briefing.readiness_state.value,
        briefing.confidence.value,
        briefing.recommendation_band.value,
        briefing.recommendation.primary,
        briefing.recommendation.avoid or "",
        " ".join(briefing.risk_flags),
        " ".join(briefing.uncertainty),
        " ".join(briefing.what_would_change_my_mind),
    ]
    for evidence in briefing.evidence:
        parts.extend(
            [
                evidence.metric,
                str(evidence.value),
                evidence.interpretation,
                evidence.source or "",
            ]
        )
    for observation in briefing.memory_observations:
        parts.extend([observation.observation, observation.relevance, observation.period or ""])
    for citation in briefing.external_citations:
        parts.extend(
            [citation.title, citation.source, citation.cited_claim, str(citation.url or "")]
        )
    for option in briefing.candidate_options:
        parts.extend([option.label, option.recommendation_band.value, option.rationale])
    for tradeoff in briefing.goal_tradeoffs:
        parts.extend([tradeoff.goal, tradeoff.tradeoff])
    for note in briefing.data_quality_notes:
        parts.extend([note.metric or "", note.note, note.severity.value])
    for alternative in briefing.alternatives:
        parts.extend([alternative.label, alternative.rationale])
    if briefing.follow_up is not None:
        parts.extend([briefing.follow_up.question, briefing.follow_up.reason])
    return " ".join(part for part in parts if part)


def _personal_evidence(items: Sequence[Mapping[str, Any]]) -> list[PersonalEvidence]:
    evidence: list[PersonalEvidence] = []
    for item in items:
        metric = str(item.get("metric") or item.get("source") or "deterministic_signal")
        evidence.append(
            PersonalEvidence(
                metric=metric,
                value=_evidence_value(item.get("value", item.get("evidence", "observed"))),
                interpretation=str(
                    item.get("interpretation") or "Used by deterministic reasoning."
                ),
                source=str(item.get("source")) if item.get("source") is not None else None,
            )
        )
    if evidence:
        return evidence
    return [
        PersonalEvidence(
            metric="deterministic_assessment",
            value="available",
            interpretation="The briefing is based on deterministic readiness rules.",
            source="reasoning_engine",
        )
    ]


def _evidence_value(value: Any) -> str | int | float | bool:
    if isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _primary_recommendation(
    assessment: Mapping[str, Any],
    explanation: LLMExplanationOutput,
) -> str:
    summary = explanation.summary.strip()
    if summary and "unavailable" not in summary.casefold():
        return summary
    state = assessment["readiness_state"].value
    band = assessment["recommendation_band"].value.replace("_", " ")
    return f"Today reads as {state}; keep the plan in the {band} range."


def _avoidance_note(assessment: Mapping[str, Any]) -> str | None:
    flags = [str(flag) for flag in assessment.get("hard_safety_flags", [])]
    if not flags:
        return None
    return "Avoid pushing through hard safety flags: " + ", ".join(flags) + "."


def _uncertainty(
    assessment: Mapping[str, Any],
    explanation: LLMExplanationOutput,
    retrieval: RetrievalResult,
) -> list[str]:
    values = [str(item) for item in assessment["uncertainty"]]
    values.extend(str(item) for item in explanation.uncertainty if str(item) not in values)
    if retrieval.degraded:
        values.append("Recent-history retrieval was unavailable, so continuity context is limited.")
    return values or ["No material uncertainty beyond normal day-to-day variability."]


def _data_quality_notes(feature: DerivedDailyFeature) -> list[DataQualityNote]:
    flags = [str(flag) for flag in feature.data_quality.get("flags", [])]
    notes = [
        DataQualityNote(
            metric=flag,
            note=f"Data quality flag present: {flag}.",
            severity=(
                DataQualitySeverity.degraded
                if flag.startswith(("missing_", "stale_"))
                else DataQualitySeverity.warning
            ),
        )
        for flag in flags
    ]
    if notes:
        return notes
    return [
        DataQualityNote(
            metric="overall_completeness",
            note="No data quality flags were raised for this feature bundle.",
            severity=DataQualitySeverity.info,
        )
    ]


def _alternatives(items: Sequence[Mapping[str, Any]]) -> list[RecommendationAlternative]:
    return [
        RecommendationAlternative(
            label=str(item.get("label") or item.get("option") or "Alternative"),
            rationale=str(item.get("rationale") or "Alternative generated by deterministic rules."),
        )
        for item in items
    ]


def _follow_up(assessment: Mapping[str, Any]) -> FollowUpPrompt | None:
    questions = assessment.get("follow_up_questions")
    if not isinstance(questions, list) or not questions:
        return None
    first = questions[0]
    if not isinstance(first, Mapping):
        return None
    return FollowUpPrompt(
        question=str(first.get("question") or "What changed since the last data point?"),
        reason=str(first.get("reason") or "More context could change the recommendation."),
    )


def _candidate_options(items: Sequence[Mapping[str, Any]]) -> list[CandidateOption]:
    options: list[CandidateOption] = []
    for item in items:
        band = item.get("recommendation_band") or item.get("band")
        if band is None:
            continue
        options.append(
            CandidateOption(
                label=str(item.get("label") or item.get("option") or str(band).replace("_", " ")),
                recommendation_band=band,
                rationale=str(
                    item.get("rationale") or "Supported by deterministic readiness rules."
                ),
            )
        )
    return options


def _goal_tradeoffs(items: Sequence[Mapping[str, Any]]) -> list[GoalTradeoff]:
    return [
        GoalTradeoff(
            goal=str(item.get("goal") or item.get("category") or "Training goal"),
            tradeoff=str(
                item.get("tradeoff") or item.get("rationale") or "Adjust intensity today."
            ),
        )
        for item in items
    ]


def _what_would_change_my_mind(uncertainty: Sequence[str]) -> list[str]:
    values = [
        "A complete morning check-in with energy, soreness, stress, and recovery scores.",
        "Fresh sleep, HRV, resting heart-rate, and recent workout data for the target day.",
    ]
    if any("stale" in item.casefold() or "missing" in item.casefold() for item in uncertainty):
        values.append("Resolving the missing or stale inputs named in the uncertainty section.")
    return values


def _data_freshness(
    session: Session,
    feature: DerivedDailyFeature,
    checkin: DailyCheckIn | None,
) -> DataFreshness:
    latest_sample = _latest_sample_at(session, feature.user_id)
    stale_sources = [
        str(flag)
        for flag in feature.data_quality.get("flags", [])
        if str(flag).startswith(("missing_", "stale_"))
    ]
    return DataFreshness(
        latest_sample_at=latest_sample,
        latest_checkin_date=checkin.date if checkin is not None else None,
        stale_sources=stale_sources,
    )


def _latest_sample_at(session: Session, user_id: UUID) -> dt.datetime | None:
    candidates: list[dt.datetime] = []
    metric = session.exec(
        select(NormalizedHealthMetric)
        .where(NormalizedHealthMetric.user_id == user_id)
        .order_by(col(NormalizedHealthMetric.start_time).desc())
    ).first()
    if metric is not None:
        candidates.append(metric.start_time)
    sleep = session.exec(
        select(SleepSession)
        .where(SleepSession.user_id == user_id)
        .where(col(SleepSession.end_time).is_not(None))
        .order_by(col(SleepSession.end_time).desc())
    ).first()
    if sleep is not None and sleep.end_time is not None:
        candidates.append(sleep.end_time)
    workout = session.exec(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user_id)
        .order_by(col(WorkoutSession.start_time).desc())
    ).first()
    if workout is not None:
        candidates.append(workout.start_time)
    return max(candidates) if candidates else None


def _first_model_run_id(result: OrchestratorResult) -> UUID | None:
    for row in result.model_runs:
        row_id = getattr(row, "id", None)
        if isinstance(row_id, UUID):
            return row_id
    return None


def _total_cost(result: OrchestratorResult) -> float:
    total = 0.0
    for row in result.model_runs:
        cost = getattr(row, "cost", None)
        if isinstance(cost, int | float):
            total += float(cost)
    return total


def _record_model_cost(result: OrchestratorResult) -> None:
    for row in result.model_runs:
        cost = getattr(row, "cost", None)
        model = getattr(row, "model_name", None)
        if isinstance(cost, int | float) and isinstance(model, str):
            add_llm_cost(float(cost), model=model)


def _recommendation_type(band: str) -> ModelRecommendationType:
    if band in {"rest", "recovery", "easy_or_recovery"}:
        return ModelRecommendationType.recovery
    return ModelRecommendationType.training


def _metric_type(value: str) -> Any:
    from baseline_api.db.models.enums import MetricType

    return MetricType(value)


def _run_type(value: str) -> Any:
    from baseline_api.db.models.enums import RunType

    return RunType(value)
