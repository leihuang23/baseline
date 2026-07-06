"""Daily briefing assembly and persistence."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlmodel import Session, col, select

from baseline_api.config import Settings
from baseline_api.db.models.assessment import (
    DailyAnalysisJob,
    ReadinessAssessment,
    ReasoningTrace,
    Recommendation,
)
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
from baseline_api.memory.service import MemoryService
from baseline_api.observability.metrics import (
    add_llm_cost,
    increment_llm_generation_result,
    observe_briefing_latency,
)
from baseline_api.observability.tracing import create_job_context, use_trace_context
from baseline_api.privacy.user import resolve_single_user
from baseline_api.reasoning.engine import ReadinessAssessmentOutput
from baseline_api.reasoning.service import ReasoningService, features_to_mapping
from baseline_api.retrieval import (
    KnowledgeChunkHit,
    KnowledgeRetrievalResult,
    KnowledgeRetrievalService,
    bind_external_claims,
    build_external_knowledge_query,
    create_embedder,
    has_external_knowledge_consent,
)
from baseline_api.safety.engine import SafetyPolicyEngine
from baseline_api.schemas.api import (
    BriefingTraceInspection,
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
    ExternalCitation,
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


@dataclass
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
    personal_degraded: bool = False
    external_degraded: bool = False
    external_hits: list[KnowledgeChunkHit] = field(default_factory=list)
    external_knowledge: list[dict[str, Any]] = field(default_factory=list)
    external_citations: list[ExternalCitation] = field(default_factory=list)
    external_uncertainty: list[str] = field(default_factory=list)
    citation_accuracy: float = 1.0


@dataclass(frozen=True)
class StageDegradation:
    stage: str
    reason: str

    def to_trace(self) -> dict[str, str]:
        return {"stage": self.stage, "reason": self.reason}


class DailyBriefingService:
    """Orchestrate features, reasoning, explanation, safety, and persistence."""

    def __init__(
        self,
        session: Session,
        *,
        llm_explainer: LLMExplainer | None = None,
        settings: Settings | None = None,
        safety_engine: SafetyPolicyEngine | None = None,
    ) -> None:
        self._session = session
        self._llm_explainer = llm_explainer
        self._settings = settings
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

    def create_daily_job(
        self,
        request: DailyAnalysisRequest,
        *,
        user: User | None = None,
    ) -> DailyAnalysisJob:
        resolved_user = self._resolve_user(user)
        user = resolved_user
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

    def get_or_create_daily_job_for_date(
        self,
        target_date: dt.date,
        *,
        user: User | None = None,
        force_recompute: bool = False,
        include_external_knowledge: bool = False,
        privacy_mode: PrivacyMode = PrivacyMode.cloud_assisted,
    ) -> DailyAnalysisJob:
        """Return the most recent daily analysis job for ``target_date`` or create one.

        Used by the API route and the fallback cron wrapper to keep the client
        trigger path idempotent: re-enqueueing the same date returns the existing
        job instead of spawning duplicate recommendations.
        """

        resolved_user = self._resolve_user(user)
        existing = self._session.exec(
            select(DailyAnalysisJob)
            .where(DailyAnalysisJob.user_id == resolved_user.id)
            .where(DailyAnalysisJob.date == target_date)
            .order_by(col(DailyAnalysisJob.created_at).desc())
        ).first()
        if existing is not None:
            status = AnalysisJobStatus(existing.status)
            if status in {AnalysisJobStatus.queued, AnalysisJobStatus.running}:
                return existing
            if status == AnalysisJobStatus.completed and not force_recompute:
                return existing
            if status == AnalysisJobStatus.failed:
                # Retry on the same job row up to ``DAILY_BRIEFING_MAX_RETRIES``.
                # If retries are exhausted, return the failed job so callers do
                # not spawn a new run until the underlying issue is resolved.
                return existing
            # A forced recompute for a completed job enqueues a fresh run with
            # the caller's latest parameters (e.g. external-knowledge opt-in).
        return self.create_daily_job(
            DailyAnalysisRequest(
                date=target_date,
                force_recompute=force_recompute,
                include_external_knowledge=include_external_knowledge,
                privacy_mode=privacy_mode,
            ),
            user=resolved_user,
        )

    def get_daily_job(
        self,
        job_id: UUID,
        *,
        user: User | None = None,
    ) -> DailyAnalysisResponse:
        resolved_user = self._resolve_user(user)
        job = self._session.get(DailyAnalysisJob, job_id)
        if job is None or job.user_id != resolved_user.id:
            raise BriefingError(
                code="analysis_job_not_found",
                message="Daily analysis job not found.",
                status_code=404,
            )
        return DailyAnalysisResponse(
            analysis_job_id=job.id,
            status=AnalysisJobStatus(job.status),
            estimated_completion_seconds=self._estimate_remaining_seconds(job),
        )

    def _estimate_remaining_seconds(self, job: DailyAnalysisJob | None) -> int:
        terminal = {
            AnalysisJobStatus.completed.value,
            AnalysisJobStatus.failed.value,
        }
        if job is None or job.status in terminal:
            return 0
        base = self._settings.daily_briefing_estimate_seconds if self._settings is not None else 90
        if job.started_at is not None:
            elapsed = (dt.datetime.now(dt.UTC) - job.started_at).total_seconds()
            return max(5, int(base - elapsed))
        return base

    def mark_daily_job_failed(
        self,
        job_id: UUID,
        *,
        error_code: str,
        error_message: str | None,
    ) -> None:
        self._mark_job_failed(
            job_id,
            error_code=error_code,
            error_message=error_message,
        )

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

        status = AnalysisJobStatus(job.status)
        if status == AnalysisJobStatus.running:
            return DailyAnalysisResponse(
                analysis_job_id=job.id,
                status=status,
                estimated_completion_seconds=self._estimate_remaining_seconds(job),
            )
        if status == AnalysisJobStatus.completed:
            if not job.force_recompute:
                return DailyAnalysisResponse(
                    analysis_job_id=job.id,
                    status=status,
                    estimated_completion_seconds=0,
                )
            request = DailyAnalysisRequest(
                date=job.date,
                force_recompute=True,
                include_external_knowledge=job.include_external_knowledge,
                privacy_mode=PrivacyMode(job.privacy_mode),
            )
            job = self.create_daily_job(request, user=user)
        if status == AnalysisJobStatus.failed:
            max_retries = (
                self._settings.daily_briefing_max_retries if self._settings is not None else 2
            )
            if job.retry_count >= max_retries:
                raise BriefingError(
                    code="analysis_job_max_retries_exceeded",
                    message="Daily briefing generation failed after maximum retries.",
                    status_code=409,
                )
            job.retry_count += 1
            job.status = AnalysisJobStatus.running.value
            job.started_at = dt.datetime.now(dt.UTC)
            job.completed_at = None
            job.error_code = None
            job.error_message = None
            job.stage_trace = [
                *job.stage_trace,
                _stage_event(
                    "job_retry",
                    trace_id=job.request_trace_id,
                    job_id=job.id,
                    retry_count=job.retry_count,
                ),
            ]
            self._session.add(job)
            self._session.commit()

        job_record_id = job.id
        user_id = user.id
        request = DailyAnalysisRequest(
            date=job.date,
            force_recompute=job.force_recompute,
            include_external_knowledge=job.include_external_knowledge,
            privacy_mode=PrivacyMode(job.privacy_mode),
        )
        context = create_job_context(
            job_id=str(job_record_id),
            trace_id=job.request_trace_id,
            internal_user_id=str(user_id),
        )
        started = time.perf_counter()
        job.status = AnalysisJobStatus.running.value
        job.started_at = dt.datetime.now(dt.UTC)
        job.stage_trace = [
            *job.stage_trace,
            _stage_event("job_running", trace_id=context.trace_id, job_id=job_record_id),
        ]
        self._session.add(job)
        self._session.commit()

        with use_trace_context(context):
            try:
                degraded_stages: list[StageDegradation] = []
                feature, feature_degradation = self._load_or_compute_features_with_degraded_mode(
                    user_id=user_id,
                    target_date=request.date,
                    force_recompute=request.force_recompute,
                )
                if feature_degradation is not None:
                    degraded_stages.append(feature_degradation)
                checkin = self._load_checkin(user_id, request.date)
                freshness, freshness_degradation = self._data_freshness_with_degraded_mode(
                    feature,
                    checkin,
                )
                if freshness_degradation is not None:
                    degraded_stages.append(freshness_degradation)
                stage_trace = [
                    *job.stage_trace,
                    _stage_event(
                        "features",
                        trace_id=context.trace_id,
                        job_id=job_record_id,
                        status="degraded" if feature_degradation else "success",
                        degraded=feature_degradation is not None,
                        degrade_reason=(
                            feature_degradation.reason if feature_degradation else None
                        ),
                        derived_daily_feature_id=str(feature.id),
                        feature_version=feature.feature_version,
                    ),
                    _stage_event(
                        "data_freshness",
                        trace_id=context.trace_id,
                        job_id=job_record_id,
                        status="degraded" if freshness_degradation else "success",
                        degraded=freshness_degradation is not None,
                        degrade_reason=(
                            freshness_degradation.reason if freshness_degradation else None
                        ),
                        data_freshness=freshness.model_dump(mode="json"),
                    ),
                ]
                active_goals = self._active_goals()
                personal_retrieval = self._retrieve_recent_history(user_id, request.date)
                if personal_retrieval.degraded:
                    degraded_stages.append(
                        StageDegradation(
                            stage="retrieval",
                            reason=personal_retrieval.degrade_reason or "retrieval_degraded",
                        )
                    )
                assessment = ReasoningService(self._session).assess_and_persist(
                    user_id=user_id,
                    derived_features=feature,
                    active_goals=active_goals,
                    recent_memory=personal_retrieval.trace_items,
                    daily_check_in=_checkin_mapping(checkin),
                    include_external_knowledge=request.include_external_knowledge,
                )
                assessment_data = _assessment_mapping(assessment)
                external_retrieval = await self._retrieve_external_knowledge(
                    user_id=user_id,
                    include_external_knowledge=request.include_external_knowledge,
                    privacy_mode=request.privacy_mode,
                    active_goals=active_goals,
                    recommendation_band=assessment.recommendation_band.value,
                )
                retrieval = _combine_retrieval(personal_retrieval, external_retrieval)
                if external_retrieval.degraded:
                    degraded_stages.append(
                        StageDegradation(
                            stage="retrieval",
                            reason=external_retrieval.degrade_reason or "retrieval_degraded",
                        )
                    )
                briefing_trace_id = str(assessment_data["reasoning_trace_id"])
                stage_trace = _retarget_stage_trace(stage_trace, trace_id=briefing_trace_id)
                stage_trace.append(
                    _stage_event(
                        "retrieval",
                        trace_id=briefing_trace_id,
                        job_id=job_record_id,
                        status="degraded" if retrieval.degraded else "success",
                        degraded=retrieval.degraded,
                        degrade_reason=retrieval.degrade_reason,
                        observation_count=len(retrieval.observations),
                        external_source_count=len(retrieval.external_citations),
                        citation_accuracy=retrieval.citation_accuracy,
                    )
                )
                stage_trace.append(
                    _stage_event(
                        "reasoning",
                        trace_id=briefing_trace_id,
                        job_id=job_record_id,
                        reasoning_trace_id=briefing_trace_id,
                        readiness_state=assessment.readiness_state.value,
                        recommendation_band=assessment.recommendation_band.value,
                    )
                )
                prompt_inputs = PromptInputs(
                    task_type=TaskType.simple_explanation,
                    request_text="Generate today's Baseline daily briefing.",
                    deterministic_assessment=assessment_data,
                    derived_features=features_to_mapping(feature),
                    retrieved_evidence=retrieval.trace_items,
                    external_knowledge=retrieval.external_knowledge,
                    raw_samples=[],
                    raw_notes=[],
                )
                explanation = await self._explain(
                    user_id=user_id,
                    prompt_inputs=prompt_inputs,
                    privacy_mode=request.privacy_mode,
                )
                if explanation.degraded:
                    degraded_stages.append(
                        StageDegradation(
                            stage="llm_explanation",
                            reason=explanation.degrade_reason or "llm_degraded",
                        )
                    )
                stage_trace.append(
                    _stage_event(
                        "llm_explanation",
                        trace_id=briefing_trace_id,
                        job_id=job_record_id,
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
                    assessment=assessment_data,
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
                        job_id=job_record_id,
                        status=briefing.safety_status.value,
                        safety_notes=briefing.safety_notes,
                    )
                )
                recommendation = self._persist_recommendation(
                    user_id=user_id,
                    target_date=request.date,
                    briefing=briefing,
                    assessment=assessment_data,
                    explanation=explanation,
                )
                briefing.recommendation_id = recommendation.id
                recommendation.briefing_payload = briefing.model_dump(mode="json")
                self._session.add(recommendation)
                memory_summary_ids = self._persist_memory_summaries(
                    user_id=user_id,
                    target_date=request.date,
                    feature=feature,
                    assessment=assessment,
                    recommendation=recommendation,
                    checkin=checkin,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                stage_trace.append(
                    _stage_event(
                        "memory",
                        trace_id=briefing_trace_id,
                        job_id=job_record_id,
                        **memory_summary_ids,
                    )
                )
                stage_trace.append(
                    _stage_event(
                        "persistence",
                        trace_id=briefing_trace_id,
                        job_id=job_record_id,
                        recommendation_id=str(recommendation.id),
                        reasoning_trace_id=briefing_trace_id,
                    )
                )
                self._record_trace(
                    trace_id=assessment_data["reasoning_trace_id"],
                    job_id=job_record_id,
                    latency_ms=latency_ms,
                    recommendation=recommendation,
                    retrieval=retrieval,
                    explanation=explanation,
                    degraded_stages=degraded_stages,
                    stage_trace=stage_trace,
                )
                job.status = AnalysisJobStatus.completed.value
                job.reasoning_trace_id = assessment_data["reasoning_trace_id"]
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
                    analysis_job_id=job_record_id,
                    status=AnalysisJobStatus.completed,
                    estimated_completion_seconds=self._estimate_remaining_seconds(
                        self._session.get(DailyAnalysisJob, job_record_id)
                    ),
                )
            except Exception as exc:
                self._session.rollback()
                self._mark_job_failed(
                    job_id,
                    error_code=type(exc).__name__,
                    error_message="Daily briefing generation failed.",
                )
                increment_llm_generation_result(status="failed")
                raise BriefingError(
                    code="daily_briefing_generation_failed",
                    message="Daily briefing generation failed.",
                    status_code=502,
                ) from exc

    def get_briefing(
        self,
        *,
        target_date: dt.date,
        offline_last: bool = False,
        user: User | None = None,
    ) -> DailyBriefingResponse:
        resolved_user = self._resolve_user(user)
        recommendation = self._latest_completed_job_recommendation(
            user_id=resolved_user.id,
            date=target_date,
            offline_last=offline_last,
        )
        if recommendation is None:
            recommendation = self._recommendations.latest_for_user_date(
                user_id=resolved_user.id,
                date=target_date,
            )
            if recommendation is None and offline_last:
                recommendation = self._recommendations.latest_for_user_on_or_before(
                    user_id=resolved_user.id,
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

    def _latest_completed_job_recommendation(
        self,
        *,
        user_id: UUID,
        date: dt.date,
        offline_last: bool,
    ) -> Recommendation | None:
        date_filter = (
            DailyAnalysisJob.date <= date if offline_last else DailyAnalysisJob.date == date
        )
        job = self._session.exec(
            select(DailyAnalysisJob)
            .where(
                DailyAnalysisJob.user_id == user_id,
                date_filter,
                DailyAnalysisJob.status == AnalysisJobStatus.completed.value,
                col(DailyAnalysisJob.recommendation_id).is_not(None),
            )
            .order_by(*_completed_job_ordering(offline_last=offline_last))
        ).first()
        if job is None or job.recommendation_id is None:
            return None
        recommendation = self._session.get(Recommendation, job.recommendation_id)
        if recommendation is None or recommendation.user_id != user_id:
            return None
        return recommendation

    def get_trace(
        self,
        trace_id: UUID,
        *,
        user: User | None = None,
    ) -> BriefingTraceInspection:
        resolved_user = self._resolve_user(user)
        trace = self._session.get(ReasoningTrace, trace_id)
        if trace is None or trace.user_id != resolved_user.id:
            raise BriefingError(
                code="trace_not_found",
                message="Briefing trace not found.",
                status_code=404,
            )
        assessment = self._session.exec(
            select(ReadinessAssessment).where(
                ReadinessAssessment.user_id == resolved_user.id,
                ReadinessAssessment.reasoning_trace_id == trace_id,
            )
        ).first()
        recommendation = self._session.exec(
            select(Recommendation)
            .where(
                Recommendation.user_id == resolved_user.id,
                Recommendation.reasoning_trace_id == trace_id,
            )
            .order_by(col(Recommendation.created_at).desc())
        ).first()
        briefing_payload = recommendation.briefing_payload if recommendation else {}
        generation = trace.trace_payload.get("briefing_generation", {})
        rules_fired = trace.rules_fired or trace.trace_payload.get("rules_fired", [])
        return BriefingTraceInspection(
            trace_id=trace.id,
            data_freshness=(
                DataFreshness.model_validate(briefing_payload["data_freshness"])
                if briefing_payload.get("data_freshness")
                else None
            ),
            feature_values=_personal_evidence(assessment.evidence_items if assessment else []),
            rules_fired=_rule_labels(rules_fired, fallback=trace.assessment_version),
            retrieved_memory=[
                MemoryObservation.model_validate(item)
                for item in briefing_payload.get("memory_observations", [])
            ],
            external_sources=[
                ExternalCitation.model_validate(item)
                for item in briefing_payload.get("external_citations", [])
            ],
            model_metadata=_model_metadata(trace.trace_payload, generation),
        )

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

        checkin = self._load_checkin(user_id, target_date)
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
            daily_check_in=_checkin_mapping(checkin),
            personal_sleep_need_hours=8.0,
            computed_at=dt.datetime.now(dt.UTC),
        )
        return _upsert_derived_daily_feature(
            self._session,
            user_id,
            target_date,
            bundle.to_derived_daily_feature_fields(),
        )

    def _load_or_compute_features_with_degraded_mode(
        self,
        *,
        user_id: UUID,
        target_date: dt.date,
        force_recompute: bool,
    ) -> tuple[DerivedDailyFeature, StageDegradation | None]:
        try:
            return (
                self._load_or_compute_features(
                    user_id=user_id,
                    target_date=target_date,
                    force_recompute=force_recompute,
                ),
                None,
            )
        except Exception as exc:
            self._session.rollback()
            reason = type(exc).__name__
            existing = self._session.exec(
                select(DerivedDailyFeature).where(
                    DerivedDailyFeature.user_id == user_id,
                    DerivedDailyFeature.date == target_date,
                )
            ).first()
            if existing is not None:
                return existing, StageDegradation(stage="features", reason=reason)
            feature = _degraded_feature(user_id=user_id, target_date=target_date, reason=reason)
            self._session.add(feature)
            self._session.flush()
            return feature, StageDegradation(stage="features", reason=reason)

    def _data_freshness_with_degraded_mode(
        self,
        feature: DerivedDailyFeature,
        checkin: DailyCheckIn | None,
    ) -> tuple[DataFreshness, StageDegradation | None]:
        try:
            with self._session.begin_nested():
                return _data_freshness(self._session, feature, checkin), None
        except Exception as exc:
            reason = type(exc).__name__
            stale_sources = [
                str(flag)
                for flag in feature.data_quality.get("flags", [])
                if str(flag).startswith(("missing_", "stale_"))
            ]
            return (
                DataFreshness(
                    latest_sample_at=None,
                    latest_checkin_date=checkin.date if checkin is not None else None,
                    stale_sources=[*stale_sources, "sync_unavailable"],
                ),
                StageDegradation(stage="sync", reason=reason),
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
            with self._session.begin_nested():
                summaries = MemoryService(self._session).recent_for_reasoning(
                    user_id=user_id,
                    target_date=target_date,
                )
        except Exception as exc:
            return RetrievalResult(
                observations=[],
                trace_items=[],
                degraded=True,
                degrade_reason=type(exc).__name__,
                personal_degraded=True,
            )

        observations = [
            MemoryObservation(
                observation=str(item["observation"]),
                relevance="Recent structured memory summary for readiness continuity.",
                period=str(item["period"]),
            )
            for item in summaries
            if item.get("observation")
        ]
        return RetrievalResult(
            observations=observations,
            trace_items=summaries,
        )

    async def _retrieve_external_knowledge(
        self,
        *,
        user_id: UUID,
        include_external_knowledge: bool,
        privacy_mode: PrivacyMode,
        active_goals: Sequence[Mapping[str, Any]],
        recommendation_band: str | None = None,
    ) -> KnowledgeRetrievalResult:
        if not include_external_knowledge:
            return KnowledgeRetrievalResult(
                hits=[],
                citations=[],
                external_knowledge=[],
                uncertainty=[],
            )
        if privacy_mode == PrivacyMode.local_only:
            return KnowledgeRetrievalResult(
                hits=[],
                citations=[],
                external_knowledge=[],
                uncertainty=[
                    "External knowledge was requested but disabled by local-only privacy mode."
                ],
            )
        if not has_external_knowledge_consent(self._session, user_id):
            return KnowledgeRetrievalResult(
                hits=[],
                citations=[],
                external_knowledge=[],
                uncertainty=["External knowledge was requested but consent is not active."],
            )
        query = build_external_knowledge_query(
            active_goals=active_goals,
            recommendation_band=recommendation_band,
            requested_scope="daily briefing training recovery sleep general research",
        )
        try:
            embedder = create_embedder(self._settings)
        except Exception as exc:
            return _external_retrieval_degraded_result(reason=type(exc).__name__)
        try:
            query_embedding = await asyncio.to_thread(embedder.embed, query)
        except Exception as exc:
            try:
                nested = self._session.begin_nested()
                try:
                    result = KnowledgeRetrievalService(self._session).retrieve_lexical_degraded(
                        query,
                        reason=type(exc).__name__,
                    )
                finally:
                    nested.rollback()
                return result
            except Exception:
                return _external_retrieval_degraded_result(reason=type(exc).__name__)
        try:
            nested = self._session.begin_nested()
            try:
                result = KnowledgeRetrievalService(self._session, embedder=embedder).retrieve(
                    query, query_embedding=query_embedding
                )
                if result.degraded and not (
                    result.hits or result.citations or result.external_knowledge
                ):
                    nested.rollback()
                    return _external_retrieval_degraded_result(
                        reason=result.degrade_reason,
                        uncertainty=result.uncertainty,
                    )
                nested.commit()
                return result
            except Exception:
                if nested.is_active:
                    nested.rollback()
                raise
        except Exception as exc:
            return _external_retrieval_degraded_result(reason=type(exc).__name__)

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
            external_citations=_external_citations_from_retrieval(
                explanation.external_citations,
                retrieval,
            ),
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
        return _enforce_served_briefing_safety(
            self._safety_engine,
            briefing,
            extra_generated_text=_llm_side_field_text(explanation),
        )

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

    def _persist_memory_summaries(
        self,
        *,
        user_id: UUID,
        target_date: dt.date,
        feature: DerivedDailyFeature,
        assessment: ReadinessAssessmentOutput,
        recommendation: Recommendation,
        checkin: DailyCheckIn | None,
    ) -> dict[str, str]:
        memory = MemoryService(self._session)
        persisted_assessment = self._assessments.get_by_user_date_trace(
            user_id=user_id,
            date=target_date,
            reasoning_trace_id=assessment.reasoning_trace_id,
        )
        if persisted_assessment is None:
            raise RuntimeError("persisted readiness assessment not found")
        daily_summary = memory.generate_daily_summary(
            user_id=user_id,
            feature=feature,
            assessment=persisted_assessment,
            recommendation=recommendation,
            checkin=checkin,
            commit=False,
        )
        weekly_summary = memory.generate_weekly_summary(
            user_id=user_id,
            start_date=target_date - dt.timedelta(days=6),
            end_date=target_date,
            commit=False,
        )
        return {
            "daily_memory_summary_id": str(daily_summary.id),
            "weekly_memory_summary_id": str(weekly_summary.id),
        }

    def _record_trace(
        self,
        *,
        trace_id: UUID,
        job_id: UUID,
        latency_ms: int,
        recommendation: Recommendation,
        retrieval: RetrievalResult,
        explanation: OrchestratorResult,
        degraded_stages: list[StageDegradation],
        stage_trace: list[dict[str, Any]],
    ) -> None:
        trace = self._session.get(ReasoningTrace, trace_id)
        if trace is None:
            return
        payload = dict(trace.trace_payload)
        generation_degraded = explanation.degraded or bool(degraded_stages)
        payload["briefing_generation"] = {
            "job_id": str(job_id),
            "recommendation_id": str(recommendation.id),
            "status": "degraded" if generation_degraded else "success",
            "degrade_reason": explanation.degrade_reason,
            "retrieval_degraded": retrieval.degraded,
            "retrieval_degrade_reason": retrieval.degrade_reason,
            "degraded_stages": [stage.to_trace() for stage in degraded_stages],
            "external_source_count": len(retrieval.external_citations),
            "external_citation_accuracy": retrieval.citation_accuracy,
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

    def _resolve_user(self, user: User | None = None) -> User:
        if user is not None:
            return user
        return resolve_single_user(
            self._session,
            empty_error_factory=lambda: BriefingError(
                code="user_not_initialized",
                message="No Baseline user is available for briefing generation.",
                status_code=409,
            ),
            ambiguous_error_factory=lambda: BriefingError(
                code="ambiguous_user",
                message="Briefing generation requires an authenticated user context.",
                status_code=409,
            ),
        )


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


def _combine_retrieval(
    personal: RetrievalResult,
    external: KnowledgeRetrievalResult,
) -> RetrievalResult:
    external_degraded = external.degraded or _has_external_retrieval_failure(external.uncertainty)
    degraded = personal.degraded or external_degraded
    degrade_reason = (
        personal.degrade_reason
        or external.degrade_reason
        or ("external_retrieval_degraded" if external_degraded else None)
    )
    external_hits = external.hits or _external_hits_from_prompt_knowledge(
        external.external_knowledge
    )
    external_citations = _retrieval_citations(external, external_hits)
    return RetrievalResult(
        observations=personal.observations,
        trace_items=personal.trace_items,
        degraded=degraded,
        degrade_reason=degrade_reason,
        personal_degraded=personal.personal_degraded or personal.degraded,
        external_degraded=external_degraded,
        external_hits=external_hits,
        external_knowledge=external.external_knowledge,
        external_citations=external_citations,
        external_uncertainty=external.uncertainty,
        citation_accuracy=(
            1.0 if external_citations and external_hits else external.citation_accuracy
        ),
    )


def _external_retrieval_degraded_result(
    *,
    reason: str | None,
    uncertainty: Sequence[str] = (),
) -> KnowledgeRetrievalResult:
    return KnowledgeRetrievalResult(
        hits=[],
        citations=[],
        external_knowledge=[],
        uncertainty=_external_retrieval_failure_uncertainty(uncertainty),
        degraded=True,
        degrade_reason=reason or "external_retrieval_degraded",
    )


def _external_retrieval_failure_uncertainty(values: Sequence[str]) -> list[str]:
    normalized = [str(item) for item in values if str(item)]
    if not _has_external_retrieval_failure(normalized):
        normalized.append(
            "External knowledge retrieval was unavailable; deterministic briefing was used."
        )
    return normalized


def _has_external_retrieval_failure(values: Sequence[str]) -> bool:
    return any("external knowledge retrieval" in str(item).casefold() for item in values)


def _completed_job_ordering(*, offline_last: bool) -> tuple[Any, ...]:
    date_order = (col(DailyAnalysisJob.date).desc(),)
    timestamp_order = (
        col(DailyAnalysisJob.completed_at).desc(),
        col(DailyAnalysisJob.started_at).desc(),
        col(DailyAnalysisJob.created_at).desc(),
    )
    external_order = (col(DailyAnalysisJob.include_external_knowledge).desc(),)
    if offline_last:
        return (*date_order, *timestamp_order, *external_order)
    return (*date_order, *external_order, *timestamp_order)


def _retrieval_citations(
    external: KnowledgeRetrievalResult,
    external_hits: Sequence[KnowledgeChunkHit],
) -> list[ExternalCitation]:
    if external.citations or not external_hits:
        return list(external.citations)
    return bind_external_claims([hit.cited_claim for hit in external_hits], external_hits).citations


def _external_hits_from_prompt_knowledge(
    external_knowledge: Sequence[Mapping[str, Any]],
) -> list[KnowledgeChunkHit]:
    hits: list[KnowledgeChunkHit] = []
    for item in external_knowledge:
        hit = _external_hit_from_prompt_knowledge(item)
        if hit is not None:
            hits.append(hit)
    return hits


def _external_hit_from_prompt_knowledge(
    item: Mapping[str, Any],
) -> KnowledgeChunkHit | None:
    try:
        return KnowledgeChunkHit(
            chunk_id=UUID(str(item["chunk_id"])),
            source_id=UUID(str(item["source_id"])),
            source_version=str(item["source_version"]),
            chunk_index=int(item["chunk_index"]),
            text=str(item["text"]),
            relevance_score=float(item["relevance_score"]),
            title=str(item["title"]),
            source=str(item["source"]),
            url_or_identifier=str(item["url_or_identifier"]),
            trust_level=str(item["trust_level"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _enforce_served_briefing_safety(
    safety_engine: SafetyPolicyEngine,
    briefing: DailyBriefingResponse,
    *,
    extra_generated_text: str = "",
) -> DailyBriefingResponse:
    result = safety_engine.evaluate(
        request_text=" ".join(briefing.risk_flags),
        generated_text=" ".join(
            part for part in [_served_briefing_text(briefing), extra_generated_text] if part
        ),
    )
    safety_notes = [result.safety_note]
    if result.status is SafetyStatus.passed:
        return briefing.model_copy(
            update={
                "safety_status": result.status,
                "safety_notes": safety_notes,
            }
        )
    if not result.triggered_categories:
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
            "uncertainty": _safety_rewrite_uncertainty(briefing.uncertainty),
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


def _safety_rewrite_uncertainty(uncertainty: Sequence[str]) -> list[str]:
    values = ["Baseline can discuss wellness signals, not medical conclusions."]
    operational_markers = (
        "external knowledge retrieval",
        "recent-history retrieval",
        "local-only privacy mode",
        "consent is not active",
    )
    for item in uncertainty:
        text = str(item)
        if any(marker in text.casefold() for marker in operational_markers):
            values.append(text)
    return list(dict.fromkeys(values))


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
        parts.extend(
            [
                tradeoff.goal,
                tradeoff.tradeoff,
                tradeoff.indicator_status or "",
                *tradeoff.evidence_refs,
                *tradeoff.missing_data,
            ]
        )
    for note in briefing.data_quality_notes:
        parts.extend([note.metric or "", note.note, note.severity.value])
    for alternative in briefing.alternatives:
        parts.extend([alternative.label, alternative.rationale])
    if briefing.follow_up is not None:
        parts.extend([briefing.follow_up.question, briefing.follow_up.reason])
    return " ".join(part for part in parts if part)


def _llm_side_field_text(explanation: LLMExplanationOutput) -> str:
    parts: list[str] = []
    for item in explanation.external_citations:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        try:
            citation = ExternalCitation.model_validate(payload)
        except ValueError:
            continue
        parts.extend(
            [citation.title, citation.source, citation.cited_claim, str(citation.url or "")]
        )
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


def _external_citations_from_retrieval(
    items: Sequence[Any],
    retrieval: RetrievalResult,
) -> list[ExternalCitation]:
    external_hits = retrieval.external_hits or _external_hits_from_prompt_knowledge(
        retrieval.external_knowledge
    )
    if not external_hits:
        return list(retrieval.external_citations)
    citations = _retrieval_hit_citations(retrieval, external_hits)
    claims: list[str] = []
    for item in items:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        try:
            claims.append(ExternalCitation.model_validate(payload).cited_claim)
        except ValueError:
            continue
    if not claims:
        return citations
    return _dedupe_external_citations(
        [*citations, *bind_external_claims(claims, external_hits).citations]
    )


def _retrieval_hit_citations(
    retrieval: RetrievalResult,
    external_hits: Sequence[KnowledgeChunkHit] | None = None,
) -> list[ExternalCitation]:
    if retrieval.external_citations:
        return list(retrieval.external_citations)
    hits = retrieval.external_hits if external_hits is None else external_hits
    return bind_external_claims(
        [hit.cited_claim for hit in hits],
        hits,
    ).citations


def _dedupe_external_citations(citations: Sequence[ExternalCitation]) -> list[ExternalCitation]:
    deduped: list[ExternalCitation] = []
    seen: set[tuple[str, str, str, str | None]] = set()
    for citation in citations:
        key = (
            citation.title,
            citation.source,
            citation.cited_claim,
            str(citation.url) if citation.url is not None else None,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(citation)
    return deduped


def _rule_labels(items: Sequence[Mapping[str, Any]], *, fallback: str | None = None) -> list[str]:
    labels: list[str] = []
    for item in items:
        rule_id = str(item.get("rule_id") or "rule")
        evidence = item.get("evidence")
        if isinstance(evidence, Mapping) and evidence:
            evidence_text = ", ".join(
                f"{key}={_evidence_value(value)}" for key, value in evidence.items()
            )
            labels.append(f"{rule_id}: {evidence_text}")
        else:
            labels.append(rule_id)
    if not labels and fallback:
        labels.append(f"deterministic_assessment: {fallback}")
    return labels


def _model_metadata(
    trace_payload: Mapping[str, Any],
    generation: Mapping[str, Any],
) -> dict[str, str]:
    metadata: dict[str, str] = {
        "assessment_version": str(trace_payload.get("assessment_version", "unknown")),
        "input_hash": str(trace_payload.get("inputs_hash", "unknown")),
        "request_route": str(trace_payload.get("request_route", "unknown")),
        "briefing_generation_status": str(generation.get("status", "unknown")),
    }
    for key in (
        "degrade_reason",
        "retrieval_degraded",
        "retrieval_degrade_reason",
        "model_run_ids",
        "total_cost",
        "latency_ms",
        "within_p95_target",
        "generated_at",
    ):
        value = generation.get(key)
        if value is not None:
            metadata[key] = str(value)
    return metadata


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
    return f"Deterministic assessment: today reads as {state}; keep the plan in the {band} range."


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
    values.extend(item for item in retrieval.external_uncertainty if item not in values)
    if retrieval.personal_degraded:
        values.append("Recent-history retrieval was unavailable, so continuity context is limited.")
    if retrieval.external_degraded:
        values.append(
            "External knowledge retrieval was unavailable, so general research context is limited."
        )
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
            indicator_status=(
                str(item["indicator_status"]) if item.get("indicator_status") is not None else None
            ),
            evidence_refs=[str(ref) for ref in item.get("evidence_refs", [])],
            missing_data=[str(value) for value in item.get("missing_data", [])],
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


def _degraded_feature(
    *,
    user_id: UUID,
    target_date: dt.date,
    reason: str,
) -> DerivedDailyFeature:
    return DerivedDailyFeature(
        user_id=user_id,
        date=target_date,
        feature_version="degraded-mode-v1",
        sleep_features=_degraded_feature_section(reason),
        hrv_features=_degraded_feature_section(reason),
        rhr_features=_degraded_feature_section(reason),
        training_load_features=_degraded_feature_section(reason),
        recovery_features=_degraded_feature_section(reason),
        goal_features=_degraded_feature_section(reason),
        data_quality={
            "flags": ["missing_feature_computation"],
            "overall_completeness": 0.0,
            "section_completeness": {},
            "degraded_mode": "feature_computation_failed",
            "degrade_reason": reason,
        },
        anomaly_flags=["feature_computation_failed"],
        computed_at=dt.datetime.now(dt.UTC),
        source_sample_ids=[],
    )


def _degraded_feature_section(reason: str) -> dict[str, Any]:
    return {
        "values": {},
        "data_quality": {
            "flags": ["missing_feature_computation"],
            "completeness": 0.0,
            "degraded_mode": "feature_computation_failed",
            "degrade_reason": reason,
        },
    }


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
