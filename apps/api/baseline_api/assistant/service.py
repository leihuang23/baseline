"""Deterministic assistant Q&A over structured personal data."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Session, col, select

from baseline_api.config import Settings
from baseline_api.db.models import (
    DerivedDailyFeature,
    MemorySummary,
    ReasoningTrace,
    Recommendation,
    User,
    WorkoutSession,
)
from baseline_api.db.models.enums import Modality
from baseline_api.privacy.user import resolve_single_user
from baseline_api.retrieval import (
    KnowledgeRetrievalResult,
    KnowledgeRetrievalService,
    build_external_knowledge_query,
    create_embedder,
    has_external_knowledge_consent,
)
from baseline_api.safety.engine import SafetyPolicyEngine
from baseline_api.schemas.api import AssistantQueryRequest, AssistantQueryResponse
from baseline_api.schemas.enums import ConfidenceLevel, DataScope, PrivacyMode, SafetyStatus
from baseline_api.schemas.recommendation import ExternalCitation, PersonalEvidence

RECOVERY_LEVELS = {
    "insufficient_data": 0.0,
    "low": 1.0,
    "moderate": 2.0,
    "mixed": 2.0,
    "high": 3.0,
}
PLAN_FEATURE_MAX_AGE_DAYS = 7


@dataclass(frozen=True)
class AssistantQueryError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class QueryPlan:
    intent: str
    metric: str | None
    period_days: int
    modality: str | None = None


@dataclass(frozen=True, slots=True)
class DraftAnswer:
    answer: str
    personal_evidence: list[PersonalEvidence]
    external_sources: list[ExternalCitation]
    confidence: ConfidenceLevel
    uncertainty: list[str]
    reused_trace_id: UUID | None = None
    trace_payload: dict[str, Any] | None = None


class AssistantQueryService:
    """Answer follow-up questions using SQL-backed personal evidence."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        safety_engine: SafetyPolicyEngine | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._safety_engine = safety_engine or SafetyPolicyEngine.from_default_policy()

    def answer(
        self,
        request: AssistantQueryRequest,
        *,
        user: User | None = None,
    ) -> AssistantQueryResponse:
        started = time.perf_counter()
        resolved_user = self._resolve_user(user)
        user = resolved_user
        target_date = request.date_context or dt.date.today()
        precheck = self._safety_engine.evaluate(
            request_text=request.question,
            generated_text="Baseline can answer wellness questions using structured data.",
        )
        if precheck.status in {SafetyStatus.blocked, SafetyStatus.escalated}:
            draft = DraftAnswer(
                answer=precheck.safe_output,
                personal_evidence=[
                    PersonalEvidence(
                        metric="safety_policy",
                        value=precheck.status.value,
                        interpretation="The question asks for medical diagnosis or treatment.",
                        source=f"safety_policy:{precheck.policy_version}",
                    )
                ],
                external_sources=[],
                confidence=ConfidenceLevel.high,
                uncertainty=[
                    "Baseline can discuss wellness signals, not medical diagnosis or treatment."
                ],
                trace_payload={"safety_result": precheck.to_dict()},
            )
        else:
            plan = _plan_query(request.question)
            external_knowledge = _external_knowledge_context(
                self._session,
                user.id,
                _external_knowledge_query(
                    active_goals=self._active_goals(),
                    question=request.question,
                ),
                request,
                settings=self._settings,
            )
            draft = self._draft_answer(
                user_id=user.id,
                target_date=target_date,
                request=request,
                plan=plan,
                external_knowledge=external_knowledge,
            )

        safety = self._evaluate_visible_response(request.question, draft)
        if safety.status is SafetyStatus.passed:
            answer = draft.answer
            personal_evidence = draft.personal_evidence
            external_sources = draft.external_sources
            confidence = draft.confidence
        else:
            answer = safety.safe_output
            personal_evidence = [
                PersonalEvidence(
                    metric="safety_policy",
                    value=safety.status.value,
                    interpretation=(
                        "The answer or supporting evidence crossed Baseline's wellness boundary."
                    ),
                    source=f"safety_policy:{safety.policy_version}",
                )
            ]
            external_sources = []
            confidence = ConfidenceLevel.high
        safety_status = safety.status
        uncertainty = list(draft.uncertainty)
        if safety.status is not SafetyStatus.passed and safety.safety_note not in uncertainty:
            uncertainty.append(safety.safety_note)
        uncertainty = _contract_uncertainty(uncertainty)

        latency_ms = int((time.perf_counter() - started) * 1000)
        trace_id = self._record_trace(
            user_id=user.id,
            target_date=target_date,
            request=request,
            draft=draft,
            answer=answer,
            personal_evidence=personal_evidence,
            external_sources=external_sources,
            safety_result=safety.to_dict(),
            latency_ms=latency_ms,
        )
        return AssistantQueryResponse(
            answer=answer,
            personal_evidence=personal_evidence,
            external_sources=external_sources,
            confidence=confidence,
            uncertainty=uncertainty,
            safety_status=safety_status,
            trace_id=trace_id,
        )

    def _draft_answer(
        self,
        *,
        user_id: UUID,
        target_date: dt.date,
        request: AssistantQueryRequest,
        plan: QueryPlan,
        external_knowledge: KnowledgeRetrievalResult,
    ) -> DraftAnswer:
        if plan.intent == "briefing_follow_up":
            return self._answer_from_briefing(user_id, target_date, request, external_knowledge)
        if plan.intent == "memory_pattern":
            return self._answer_from_memory(user_id, target_date, request, external_knowledge)
        if plan.intent == "candidate_plan":
            return self._candidate_week_plan(user_id, target_date, request, external_knowledge)
        if plan.intent == "modality":
            return self._answer_modality(user_id, target_date, request, plan, external_knowledge)
        if plan.intent == "compare_periods":
            return self._answer_compare_periods(
                user_id, target_date, request, plan, external_knowledge
            )
        return self._answer_recent_history(user_id, target_date, request, plan, external_knowledge)

    def _answer_from_briefing(
        self,
        user_id: UUID,
        target_date: dt.date,
        request: AssistantQueryRequest,
        external_knowledge: KnowledgeRetrievalResult,
    ) -> DraftAnswer:
        if DataScope.briefing_trace not in request.allowed_data_scope:
            return _insufficient(
                "I do not have permission to use the briefing trace for this follow-up.",
                metric="briefing_trace",
            )
        recommendation = self._recommendation_for_date(user_id, target_date)
        if recommendation is None or not recommendation.briefing_payload:
            return _insufficient(
                "Not enough data: I could not find a briefing trace for that date.",
                metric="briefing_trace",
            )

        payload = recommendation.briefing_payload
        evidence = _personal_evidence_from_payload(payload.get("evidence", []))
        if not evidence:
            return _insufficient(
                "Not enough data: the briefing trace did not include personal evidence.",
                metric="briefing_trace",
            )
        recommendation_text = str(payload.get("recommendation", {}).get("primary", "")).strip()
        risk_flags = [str(item) for item in payload.get("risk_flags", [])]
        evidence_text = "; ".join(
            f"{item.metric}={item.value} ({item.interpretation})" for item in evidence[:3]
        )
        answer = (
            "The briefing's structured trace points to a conservative choice because "
            f"{evidence_text}."
        )
        if recommendation_text:
            answer += f" The stored recommendation was: {recommendation_text}"
        if risk_flags:
            answer += f" Trace risk flags: {', '.join(risk_flags)}."
        return DraftAnswer(
            answer=answer,
            personal_evidence=evidence,
            external_sources=list(external_knowledge.citations),
            confidence=ConfidenceLevel.medium,
            uncertainty=list(external_knowledge.uncertainty),
            reused_trace_id=recommendation.reasoning_trace_id,
            trace_payload={
                "recommendation_id": str(recommendation.id),
                "reasoning_trace_id": (
                    str(recommendation.reasoning_trace_id)
                    if recommendation.reasoning_trace_id
                    else None
                ),
                "date": recommendation.date.isoformat(),
                "table": "recommendation",
                "evidence_sources": [item.source for item in evidence],
                "intent": "briefing_follow_up",
            },
        )

    def _answer_from_memory(
        self,
        user_id: UUID,
        target_date: dt.date,
        request: AssistantQueryRequest,
        external_knowledge: KnowledgeRetrievalResult,
    ) -> DraftAnswer:
        if DataScope.memory not in request.allowed_data_scope:
            return _insufficient(
                "I do not have permission to read memory summaries for this question.",
                metric="memory",
            )
        rows = self._session.exec(
            select(MemorySummary)
            .where(MemorySummary.user_id == user_id, MemorySummary.end_date <= target_date)
            .order_by(col(MemorySummary.end_date).desc(), col(MemorySummary.created_at).desc())
            .limit(4)
        ).all()
        evidence: list[PersonalEvidence] = []
        observations: list[str] = []
        for row in rows:
            for observation in [*row.observations, *row.hypotheses]:
                text = _observation_text(observation)
                if not text:
                    continue
                observations.append(text)
                evidence.append(
                    PersonalEvidence(
                        metric="memory_summary",
                        value=f"{row.start_date.isoformat()}..{row.end_date.isoformat()}",
                        interpretation=text,
                        source=f"memory_summary:{row.id}",
                    )
                )
                if len(evidence) >= 3:
                    break
            if len(evidence) >= 3:
                break
        if not evidence:
            return _insufficient(
                "Not enough data: I do not have memory summaries with learned patterns yet.",
                metric="memory",
            )
        return DraftAnswer(
            answer="The learned pattern summaries currently say: " + " ".join(observations[:3]),
            personal_evidence=evidence,
            external_sources=list(external_knowledge.citations),
            confidence=ConfidenceLevel.medium,
            uncertainty=[
                "Memory summaries are compressed observations and should be checked against "
                "raw trends."
            ]
            + list(external_knowledge.uncertainty),
            trace_payload={
                "memory_summary_count": len(rows),
                "intent": "memory_pattern",
                "table": "memory_summary",
                "row_ids": [str(row.id) for row in rows],
                "periods": [
                    {
                        "start_date": row.start_date.isoformat(),
                        "end_date": row.end_date.isoformat(),
                        "period_type": row.period_type.value,
                    }
                    for row in rows
                ],
            },
        )

    def _candidate_week_plan(
        self,
        user_id: UUID,
        target_date: dt.date,
        request: AssistantQueryRequest,
        external_knowledge: KnowledgeRetrievalResult,
    ) -> DraftAnswer:
        feature = (
            self._latest_feature(user_id, target_date)
            if DataScope.recent_health in request.allowed_data_scope
            else None
        )
        if feature is not None and feature.date < target_date - dt.timedelta(
            days=PLAN_FEATURE_MAX_AGE_DAYS - 1
        ):
            feature = None
        recommendation = (
            self._recommendation_for_date(user_id, target_date)
            if DataScope.briefing_trace in request.allowed_data_scope
            else None
        )
        if feature is None and recommendation is None:
            return _insufficient(
                "Not enough data: I need recent features or a briefing before sketching options.",
                metric="candidate_plan",
            )

        evidence = self._plan_evidence(feature, recommendation)
        if not evidence:
            return _insufficient(
                "Not enough data: recent context exists, but it does not contain usable "
                "personal evidence for weekly planning options.",
                metric="candidate_plan",
            )
        answer = (
            "Candidate plan, not a prescription: keep the week flexible with one easier day, "
            "one moderate aerobic option, one strength or mobility option, and at least one "
            "recovery buffer. Choose the easier option if sleep debt, resting heart rate, "
            "soreness, or perceived recovery worsens."
        )
        if recommendation is not None and recommendation.candidate_options:
            labels = [
                str(option.get("label", "")).strip()
                for option in recommendation.candidate_options
                if str(option.get("label", "")).strip()
            ]
            if labels:
                answer += f" Today's briefing options to consider: {', '.join(labels[:3])}."
        return DraftAnswer(
            answer=answer,
            personal_evidence=evidence,
            external_sources=list(external_knowledge.citations),
            confidence=ConfidenceLevel.medium,
            uncertainty=[
                "This is an option set for planning, not a prescription, medical treatment, "
                "or coaching instruction.",
                "Update the plan as the week's data changes.",
            ]
            + list(external_knowledge.uncertainty),
            reused_trace_id=recommendation.reasoning_trace_id if recommendation else None,
            trace_payload={
                "intent": "candidate_plan",
                "feature_row_id": str(feature.id) if feature else None,
                "feature_date": feature.date.isoformat() if feature else None,
                "recommendation_id": str(recommendation.id) if recommendation else None,
                "recommendation_date": (
                    recommendation.date.isoformat() if recommendation else None
                ),
            },
        )

    def _answer_modality(
        self,
        user_id: UUID,
        target_date: dt.date,
        request: AssistantQueryRequest,
        plan: QueryPlan,
        external_knowledge: KnowledgeRetrievalResult,
    ) -> DraftAnswer:
        if DataScope.recent_health not in request.allowed_data_scope:
            return _insufficient(
                "I do not have permission to use recent workout history.",
                metric="workout_session",
            )
        if plan.modality is None:
            return _insufficient("Not enough data: I could not identify a workout modality.")
        modality = Modality(plan.modality)

        start = dt.datetime.combine(
            target_date - dt.timedelta(days=plan.period_days - 1),
            dt.time.min,
            tzinfo=dt.UTC,
        )
        end = dt.datetime.combine(target_date + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)
        rows = self._session.exec(
            select(WorkoutSession)
            .where(
                WorkoutSession.user_id == user_id,
                WorkoutSession.start_time >= start,
                WorkoutSession.start_time < end,
                WorkoutSession.modality == modality,
            )
            .order_by(col(WorkoutSession.start_time).desc())
        ).all()
        if not rows:
            return _insufficient(
                f"Not enough data: I found no {plan.modality} workouts in the recent window.",
                metric="workout_session",
            )
        total_minutes = round(sum(row.duration for row in rows) / 60, 1)
        avg_hr_values = [row.average_hr for row in rows if row.average_hr is not None]
        avg_hr = round(sum(avg_hr_values) / len(avg_hr_values), 1) if avg_hr_values else None
        evidence = [
            PersonalEvidence(
                metric=f"{plan.modality}_workout_count",
                value=len(rows),
                interpretation=f"{len(rows)} {plan.modality} sessions in {plan.period_days} days.",
                source="workout_session",
            ),
            PersonalEvidence(
                metric=f"{plan.modality}_duration_minutes",
                value=total_minutes,
                interpretation="Total recorded duration for the selected modality.",
                source="workout_session.duration",
            ),
        ]
        if avg_hr is not None:
            evidence.append(
                PersonalEvidence(
                    metric=f"{plan.modality}_average_hr",
                    value=avg_hr,
                    interpretation="Mean workout heart rate across sessions with HR data.",
                    source="workout_session.average_hr",
                )
            )
        answer = (
            f"Your recent {plan.modality} history shows {len(rows)} sessions totaling "
            f"{total_minutes} minutes over {plan.period_days} days."
        )
        if avg_hr is not None:
            answer += f" Average recorded workout HR was {avg_hr} bpm."
        return DraftAnswer(
            answer=answer,
            personal_evidence=evidence,
            external_sources=list(external_knowledge.citations),
            confidence=ConfidenceLevel.high,
            uncertainty=_contract_uncertainty(list(external_knowledge.uncertainty)),
            trace_payload={
                "intent": "modality",
                "table": "workout_session",
                "row_count": len(rows),
                "row_ids": [str(row.id) for row in rows],
                "date_range": {
                    "start": start.isoformat(),
                    "end_exclusive": end.isoformat(),
                },
                "modality": plan.modality,
                "aggregation": {
                    "duration_minutes": "sum(duration_seconds) / 60",
                    "average_hr": "mean(average_hr) for rows with HR data",
                },
            },
        )

    def _answer_compare_periods(
        self,
        user_id: UUID,
        target_date: dt.date,
        request: AssistantQueryRequest,
        plan: QueryPlan,
        external_knowledge: KnowledgeRetrievalResult,
    ) -> DraftAnswer:
        if DataScope.recent_health not in request.allowed_data_scope:
            return _insufficient(
                "I do not have permission to use recent derived health features.",
                metric="derived_daily_feature",
            )
        if plan.metric is None:
            return _insufficient(
                "Not enough data: I could not map this question to a supported structured metric.",
                metric="supported_metric",
            )
        metric = plan.metric
        current_start = target_date - dt.timedelta(days=plan.period_days - 1)
        previous_end = current_start - dt.timedelta(days=1)
        previous_start = previous_end - dt.timedelta(days=plan.period_days - 1)
        current_rows = self._features_between(user_id, current_start, target_date)
        previous_rows = self._features_between(user_id, previous_start, previous_end)
        current_values = [_metric_value(row, metric) for row in current_rows]
        previous_values = [_metric_value(row, metric) for row in previous_rows]
        current_numeric = [value for value in current_values if value is not None]
        previous_numeric = [value for value in previous_values if value is not None]
        if not current_numeric or not previous_numeric:
            return _insufficient(
                "Not enough data: both comparison periods need grounded derived features.",
                metric=metric,
            )
        current_avg = round(sum(current_numeric) / len(current_numeric), 2)
        previous_avg = round(sum(previous_numeric) / len(previous_numeric), 2)
        delta = round(current_avg - previous_avg, 2)
        direction = "increased" if delta > 0 else "decreased" if delta < 0 else "was unchanged"
        evidence = [
            PersonalEvidence(
                metric=f"{metric}.current_average",
                value=current_avg,
                interpretation=(
                    f"{len(current_numeric)} usable days from {current_start} to {target_date}."
                ),
                source="derived_daily_feature",
            ),
            PersonalEvidence(
                metric=f"{metric}.previous_average",
                value=previous_avg,
                interpretation=(
                    f"{len(previous_numeric)} usable days from {previous_start} to {previous_end}."
                ),
                source="derived_daily_feature",
            ),
            PersonalEvidence(
                metric=f"{metric}.delta",
                value=delta,
                interpretation=f"Current period {direction} versus the prior period.",
                source="derived_daily_feature",
            ),
        ]
        return DraftAnswer(
            answer=(
                f"{_metric_label(metric)} {direction} by {abs(delta)}: current-period "
                f"average {current_avg} versus previous-period average {previous_avg}."
            ),
            personal_evidence=evidence,
            external_sources=list(external_knowledge.citations),
            confidence=_confidence_for_counts(len(current_numeric), len(previous_numeric)),
            uncertainty=_period_uncertainty(
                plan.period_days,
                len(current_numeric),
                len(previous_numeric),
            )
            + list(external_knowledge.uncertainty),
            trace_payload={
                "intent": "compare_periods",
                "table": "derived_daily_feature",
                "metric": metric,
                "metric_path": _metric_path(metric),
                "aggregation": "average(current_period) - average(previous_period)",
                "current_row_ids": [str(row.id) for row in current_rows],
                "previous_row_ids": [str(row.id) for row in previous_rows],
                "current_dates": [row.date.isoformat() for row in current_rows],
                "previous_dates": [row.date.isoformat() for row in previous_rows],
                "current_rows": len(current_rows),
                "previous_rows": len(previous_rows),
            },
        )

    def _answer_recent_history(
        self,
        user_id: UUID,
        target_date: dt.date,
        request: AssistantQueryRequest,
        plan: QueryPlan,
        external_knowledge: KnowledgeRetrievalResult,
    ) -> DraftAnswer:
        if DataScope.recent_health not in request.allowed_data_scope:
            return _insufficient(
                "I do not have permission to use recent derived health features.",
                metric="derived_daily_feature",
            )
        if plan.metric is None:
            return _insufficient(
                "Not enough data: I could not map this question to a supported structured metric.",
                metric="supported_metric",
            )
        metric = plan.metric
        start = target_date - dt.timedelta(days=plan.period_days - 1)
        rows = self._features_between(user_id, start, target_date)
        values = [_metric_value(row, metric) for row in rows]
        numeric_values = [value for value in values if value is not None]
        if not numeric_values:
            return _insufficient(
                "Not enough data: I could not ground this question in derived daily features.",
                metric=metric,
            )
        latest = numeric_values[-1]
        average = round(sum(numeric_values) / len(numeric_values), 2)
        evidence = [
            PersonalEvidence(
                metric=f"{metric}.latest",
                value=latest,
                interpretation=f"Latest usable value on or before {target_date.isoformat()}.",
                source="derived_daily_feature",
            ),
            PersonalEvidence(
                metric=f"{metric}.average",
                value=average,
                interpretation=f"Average across {len(numeric_values)} usable days.",
                source="derived_daily_feature",
            ),
        ]
        return DraftAnswer(
            answer=(
                f"{_metric_label(metric)} averaged {average} over the recent "
                f"{plan.period_days}-day window, with latest usable value {latest}."
            ),
            personal_evidence=evidence,
            external_sources=list(external_knowledge.citations),
            confidence=_confidence_for_single_count(len(numeric_values)),
            uncertainty=_period_uncertainty(plan.period_days, len(numeric_values), None)
            + list(external_knowledge.uncertainty),
            trace_payload={
                "intent": "recent_history",
                "table": "derived_daily_feature",
                "metric": metric,
                "metric_path": _metric_path(metric),
                "row_count": len(rows),
                "row_ids": [str(row.id) for row in rows],
                "dates": [row.date.isoformat() for row in rows],
                "usable_dates": [
                    row.date.isoformat()
                    for row, value in zip(rows, values, strict=False)
                    if value is not None
                ],
                "aggregation": "latest value and arithmetic average",
            },
        )

    def _features_between(
        self,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
    ) -> list[DerivedDailyFeature]:
        return list(
            self._session.exec(
                select(DerivedDailyFeature)
                .where(
                    DerivedDailyFeature.user_id == user_id,
                    DerivedDailyFeature.date >= start_date,
                    DerivedDailyFeature.date <= end_date,
                )
                .order_by(col(DerivedDailyFeature.date))
            ).all()
        )

    def _latest_feature(self, user_id: UUID, target_date: dt.date) -> DerivedDailyFeature | None:
        return self._session.exec(
            select(DerivedDailyFeature)
            .where(DerivedDailyFeature.user_id == user_id, DerivedDailyFeature.date <= target_date)
            .order_by(
                col(DerivedDailyFeature.date).desc(),
                col(DerivedDailyFeature.created_at).desc(),
            )
        ).first()

    def _recommendation_for_date(
        self,
        user_id: UUID,
        target_date: dt.date,
    ) -> Recommendation | None:
        return self._session.exec(
            select(Recommendation)
            .where(Recommendation.user_id == user_id, Recommendation.date == target_date)
            .order_by(col(Recommendation.created_at).desc())
        ).first()

    def _plan_evidence(
        self,
        feature: DerivedDailyFeature | None,
        recommendation: Recommendation | None,
    ) -> list[PersonalEvidence]:
        evidence: list[PersonalEvidence] = []
        if feature is not None:
            for metric in ("sleep_debt_hours", "rhr_deviation_pct", "recovery_level"):
                value = _metric_value(feature, metric)
                if value is None:
                    continue
                evidence.append(
                    PersonalEvidence(
                        metric=metric,
                        value=value,
                        interpretation="Recent derived feature used to frame candidate options.",
                        source=f"derived_daily_feature:{feature.id}",
                    )
                )
        if recommendation is not None and recommendation.recommendation_text:
            evidence.append(
                PersonalEvidence(
                    metric="latest_recommendation",
                    value=recommendation.date.isoformat(),
                    interpretation=recommendation.recommendation_text,
                    source=f"recommendation:{recommendation.id}",
                )
            )
        return evidence[:4]

    def _record_trace(
        self,
        *,
        user_id: UUID,
        target_date: dt.date,
        request: AssistantQueryRequest,
        draft: DraftAnswer,
        answer: str,
        personal_evidence: list[PersonalEvidence],
        external_sources: list[ExternalCitation],
        safety_result: dict[str, Any],
        latency_ms: int,
    ) -> UUID:
        trace_id = draft.reused_trace_id or uuid4()
        trace = self._session.get(ReasoningTrace, trace_id)
        assistant_payload = {
            "question": request.question,
            "date_context": target_date.isoformat(),
            "allowed_data_scope": [scope.value for scope in request.allowed_data_scope],
            "include_external_knowledge": request.include_external_knowledge,
            "privacy_mode": request.privacy_mode.value,
            "answer": answer,
            "personal_evidence_count": len(personal_evidence),
            "personal_evidence_sources": [item.source for item in personal_evidence],
            "external_source_count": len(external_sources),
            "external_sources": [source.model_dump(mode="json") for source in external_sources],
            "confidence": draft.confidence.value,
            "uncertainty": draft.uncertainty,
            "safety_result": safety_result,
            "latency_ms": latency_ms,
            "p95_target_ms": 15_000,
            "within_p95_target": latency_ms < 15_000,
            "retrieval": draft.trace_payload or {},
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        if trace is None:
            trace = ReasoningTrace(
                id=trace_id,
                user_id=user_id,
                date=target_date,
                trace_version="assistant_query_v1",
                assessment_version="assistant_query_v1",
                input_hash=_hash_json(assistant_payload),
                rules_fired=[],
                hard_safety_flags=list(safety_result.get("triggered_categories", [])),
                trace_payload={"assistant_queries": [assistant_payload]},
            )
        else:
            payload = dict(trace.trace_payload)
            existing_queries = payload.get("assistant_queries", [])
            queries = existing_queries if isinstance(existing_queries, list) else []
            payload["assistant_queries"] = [*queries, assistant_payload]
            trace.trace_payload = payload
        self._session.add(trace)
        self._session.commit()
        return trace_id

    def _evaluate_visible_response(
        self,
        question: str,
        draft: DraftAnswer,
    ) -> Any:
        visible_text = " ".join(
            [
                draft.answer,
                " ".join(
                    f"{item.metric} {item.value} {item.interpretation} {item.source or ''}"
                    for item in draft.personal_evidence
                ),
                " ".join(
                    f"{item.title} {item.source} {item.cited_claim}"
                    for item in draft.external_sources
                ),
            ]
        )
        return self._safety_engine.evaluate(
            request_text=question,
            generated_text=visible_text,
        )

    def _resolve_user(self, user: User | None = None) -> User:
        if user is not None:
            return user
        return resolve_single_user(
            self._session,
            empty_error_factory=lambda: AssistantQueryError(
                code="user_not_initialized",
                message="No Baseline user is available for assistant queries.",
                status_code=409,
            ),
            ambiguous_error_factory=lambda: AssistantQueryError(
                code="ambiguous_user",
                message="Assistant queries require an authenticated user context.",
                status_code=409,
            ),
        )

    def _active_goals(self) -> list[dict[str, Any]]:
        from baseline_api.goals import GoalService

        goal_set = GoalService(self._session).get_active_goal_set()
        return [goal.model_dump(mode="json") for goal in goal_set.goals]


def _plan_query(question: str) -> QueryPlan:
    normalized = question.casefold()
    metric = _metric_from_question(normalized)
    period_days = 30 if "month" in normalized else 7
    modality = _modality_from_question(normalized)
    if any(
        term in normalized for term in ("why not", "briefing", "recommendation", "today")
    ) and any(term in normalized for term in ("tempo", "hard", "workout", "training", "why not")):
        return QueryPlan("briefing_follow_up", metric, period_days, modality)
    if any(term in normalized for term in ("pattern", "learn about me", "learned about me")):
        return QueryPlan("memory_pattern", metric, period_days, modality)
    if "plan" in normalized and any(term in normalized for term in ("week", "this week")):
        return QueryPlan("candidate_plan", metric, period_days, modality)
    if modality is not None:
        return QueryPlan("modality", metric, period_days, modality)
    if any(term in normalized for term in ("compare", "change", "changed", "versus", "vs")):
        return QueryPlan("compare_periods", metric, period_days, modality)
    return QueryPlan("recent_history", metric, period_days, modality)


def _metric_from_question(normalized: str) -> str | None:
    if "sleep" in normalized:
        return "sleep_debt_hours"
    if "hrv" in normalized or "heart rate variability" in normalized:
        return "hrv_deviation_pct"
    if "resting heart" in normalized or "rhr" in normalized:
        return "rhr_deviation_pct"
    if "load" in normalized or "training" in normalized or "workout" in normalized:
        return "acute_chronic_ratio"
    if "recovery" in normalized:
        return "recovery_level"
    return None


def _modality_from_question(normalized: str) -> str | None:
    modalities = {
        "run": ("run", "running", "tempo"),
        "cycle": ("cycle", "cycling", "bike"),
        "swim": ("swim", "swimming"),
        "strength": ("strength", "lift", "lifting"),
        "yoga": ("yoga",),
        "mobility": ("mobility",),
        "hiit": ("hiit",),
    }
    for modality, terms in modalities.items():
        if any(term in normalized for term in terms):
            return modality
    return None


def _metric_value(row: DerivedDailyFeature, metric: str) -> float | None:
    if metric == "sleep_debt_hours":
        return _numeric_feature(row.sleep_features, "sleep_debt_hours")
    if metric == "hrv_deviation_pct":
        return _numeric_feature(row.hrv_features, "deviation_pct")
    if metric == "rhr_deviation_pct":
        return _numeric_feature(row.rhr_features, "deviation_pct")
    if metric == "acute_chronic_ratio":
        return _numeric_feature(row.training_load_features, "acute_chronic_ratio")
    if metric == "recovery_level":
        level = _feature_raw_value(row.recovery_features, "level")
        return RECOVERY_LEVELS.get(str(level))
    return None


def _numeric_feature(features: Mapping[str, Any], key: str) -> float | None:
    value = _feature_raw_value(features, key)
    if isinstance(value, int | float):
        return float(value)
    return None


def _feature_raw_value(features: Mapping[str, Any], key: str) -> Any:
    values = features.get("values")
    if not isinstance(values, Mapping):
        return None
    item = values.get(key)
    if isinstance(item, Mapping):
        return item.get("value")
    return None


def _metric_label(metric: str) -> str:
    return {
        "sleep_debt_hours": "Sleep debt",
        "hrv_deviation_pct": "HRV deviation",
        "rhr_deviation_pct": "Resting-heart-rate deviation",
        "acute_chronic_ratio": "Training load balance",
        "recovery_level": "Recovery level",
    }.get(metric, metric)


def _metric_path(metric: str) -> str:
    return {
        "sleep_debt_hours": "sleep_features.values.sleep_debt_hours.value",
        "hrv_deviation_pct": "hrv_features.values.deviation_pct.value",
        "rhr_deviation_pct": "rhr_features.values.deviation_pct.value",
        "acute_chronic_ratio": "training_load_features.values.acute_chronic_ratio.value",
        "recovery_level": "recovery_features.values.level.value",
    }.get(metric, metric)


def _personal_evidence_from_payload(items: Any) -> list[PersonalEvidence]:
    if not isinstance(items, list):
        return []
    evidence: list[PersonalEvidence] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        try:
            evidence.append(PersonalEvidence.model_validate(item))
        except ValueError:
            continue
    return evidence


def _observation_text(observation: Mapping[str, Any]) -> str:
    for key in ("observation", "hypothesis", "summary", "text"):
        value = observation.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if observation:
        return json.dumps(dict(observation), sort_keys=True)
    return ""


def _insufficient(message: str, *, metric: str = "data_availability") -> DraftAnswer:
    return DraftAnswer(
        answer=message,
        personal_evidence=[
            PersonalEvidence(
                metric=metric,
                value="not_enough_data",
                interpretation=message,
                source=None,
            )
        ],
        external_sources=[],
        confidence=ConfidenceLevel.low,
        uncertainty=["Not enough structured personal data was available to ground the answer."],
        trace_payload={"intent": "insufficient_data"},
    )


def _external_knowledge_context(
    session: Session,
    user_id: UUID,
    query: str,
    request: AssistantQueryRequest,
    *,
    settings: Settings | None = None,
) -> KnowledgeRetrievalResult:
    if not request.include_external_knowledge:
        return KnowledgeRetrievalResult(
            hits=[],
            citations=[],
            external_knowledge=[],
            uncertainty=[],
        )
    if request.privacy_mode == PrivacyMode.local_only:
        return KnowledgeRetrievalResult(
            hits=[],
            citations=[],
            external_knowledge=[],
            uncertainty=[
                "External knowledge was requested but disabled by local-only privacy mode."
            ],
            degraded=True,
            degrade_reason="privacy_mode_local_only",
        )
    if DataScope.external_knowledge not in request.allowed_data_scope:
        return KnowledgeRetrievalResult(
            hits=[],
            citations=[],
            external_knowledge=[],
            uncertainty=["External knowledge was requested but not allowed by data scope."],
        )
    if not has_external_knowledge_consent(session, user_id):
        return KnowledgeRetrievalResult(
            hits=[],
            citations=[],
            external_knowledge=[],
            uncertainty=["External knowledge was requested but consent is not active."],
        )
    try:
        return KnowledgeRetrievalService(
            session,
            embedder=create_embedder(settings),
        ).retrieve(query)
    except Exception as exc:
        return KnowledgeRetrievalResult(
            hits=[],
            citations=[],
            external_knowledge=[],
            uncertainty=[
                "External knowledge retrieval was unavailable; no external claims were used."
            ],
            degraded=True,
            degrade_reason=type(exc).__name__,
        )


def _external_knowledge_query(
    *,
    active_goals: list[dict[str, Any]],
    question: str | None = None,
) -> str:
    return build_external_knowledge_query(
        active_goals=active_goals,
        question=question,
        requested_scope="assistant wellness question general research",
    )


def _contract_uncertainty(uncertainty: list[str]) -> list[str]:
    if uncertainty:
        return uncertainty
    return ["No material grounding gaps in the retrieved structured data."]


def _period_uncertainty(
    period_days: int,
    current_count: int,
    previous_count: int | None,
) -> list[str]:
    uncertainty: list[str] = []
    if current_count < period_days:
        uncertainty.append(f"Only {current_count} of {period_days} days had usable current data.")
    if previous_count is not None and previous_count < period_days:
        uncertainty.append(f"Only {previous_count} of {period_days} days had usable prior data.")
    if not uncertainty:
        uncertainty.append("No material grounding gaps in the retrieved structured data.")
    return uncertainty


def _confidence_for_counts(current_count: int, previous_count: int) -> ConfidenceLevel:
    if current_count >= 5 and previous_count >= 5:
        return ConfidenceLevel.high
    if current_count >= 2 and previous_count >= 2:
        return ConfidenceLevel.medium
    return ConfidenceLevel.low


def _confidence_for_single_count(count: int) -> ConfidenceLevel:
    if count >= 5:
        return ConfidenceLevel.high
    if count >= 2:
        return ConfidenceLevel.medium
    return ConfidenceLevel.low


def _hash_json(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
