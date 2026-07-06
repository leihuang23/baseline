"""Recommendation feedback capture and outcome routing.

Feedback is an input to personal memory and evaluation only. This module must
not write to safety policy artifacts or reinterpret safety rules.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from fastapi import status
from sqlmodel import Session, col, select

from baseline_api.db.models.assessment import Recommendation
from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.models.enums import (
    AuditEventType,
    PeriodType,
    RecommendationBand,
    RedactionStatus,
)
from baseline_api.db.models.evaluation import EvaluationCase
from baseline_api.db.models.memory import MemorySummary
from baseline_api.db.models.user import User
from baseline_api.db.repositories.assessment import RecommendationRepository
from baseline_api.db.repositories.audit import AuditEventRepository
from baseline_api.db.repositories.evaluation import EvaluationCaseRepository
from baseline_api.db.repositories.memory import MemorySummaryRepository
from baseline_api.schemas.api import (
    FeedbackContradictionAlert,
    RecommendationFeedbackRequest,
    RecommendationFeedbackResponse,
)
from baseline_api.schemas.enums import (
    EvalQueueStatus,
    FeedbackRating,
    MemoryUpdateStatus,
)

FEEDBACK_MEMORY_VERSION = "feedback-outcome-v1"
FEEDBACK_EVAL_SCENARIO = "recommendation_feedback_usefulness"
SAFETY_POLICY_MUTATION_ALLOWED = False
_CONTRADICTION_THRESHOLD = 2
_CONSERVATIVE_BANDS = {
    RecommendationBand.easy,
    RecommendationBand.easy_or_recovery,
    RecommendationBand.recovery,
    RecommendationBand.rest,
}
_AGGRESSIVE_BANDS = {
    RecommendationBand.hard_training_ok,
    RecommendationBand.moderate,
    RecommendationBand.moderate_or_upper_body,
}

JsonDict = dict[str, Any]


class FeedbackError(Exception):
    """Raised when feedback cannot be applied."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class FeedbackService:
    """Capture recommendation feedback and route it to memory and evals."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._recommendations = RecommendationRepository(session)
        self._memory = MemorySummaryRepository(session)
        self._evals = EvaluationCaseRepository(session)
        self._audits = AuditEventRepository(session)

    def submit_feedback(
        self,
        recommendation_id: UUID,
        request: RecommendationFeedbackRequest,
        *,
        user: User | None = None,
    ) -> RecommendationFeedbackResponse:
        recommendation = self._recommendations.get_by_id(recommendation_id)
        if recommendation is None:
            raise FeedbackError(
                code="recommendation_not_found",
                message="Recommendation was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if user is not None and recommendation.user_id != user.id:
            raise FeedbackError(
                code="recommendation_not_found",
                message="Recommendation was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        feedback_id = uuid4()
        recorded_at = dt.datetime.now(dt.UTC)
        structured_signal = _structured_wrong_because(request, recommendation)
        contradiction_alert = self._contradiction_alert(
            recommendation=recommendation,
            structured_signal=structured_signal,
        )
        feedback_payload = _feedback_payload(
            feedback_id=feedback_id,
            recommendation=recommendation,
            request=request,
            structured_signal=structured_signal,
            contradiction_alert=contradiction_alert,
            recorded_at=recorded_at,
        )

        recommendation.accepted_action = _accepted_action_payload(
            feedback_id=feedback_id,
            recommendation_id=recommendation.id,
            action_taken=request.action_taken.value,
            recorded_at=recorded_at,
        )
        recommendation.user_feedback = feedback_payload
        recommendation.updated_at = recorded_at
        self._session.add(recommendation)

        memory_status = self._apply_memory_update(
            recommendation=recommendation,
            feedback_payload=feedback_payload,
            structured_signal=structured_signal,
            recorded_at=recorded_at,
        )
        eval_status = self._enqueue_eval(
            recommendation=recommendation,
            feedback_payload=feedback_payload,
            structured_signal=structured_signal,
            contradiction_alert=contradiction_alert,
            memory_status=memory_status,
            recorded_at=recorded_at,
        )
        self._emit_audit(
            recommendation=recommendation,
            feedback_id=feedback_id,
            feedback_payload=feedback_payload,
            structured_signal=structured_signal,
            contradiction_alert=contradiction_alert,
            recorded_at=recorded_at,
        )
        recommendation.user_feedback = {
            **feedback_payload,
            "memory_update_status": memory_status.value,
            "eval_queue_status": eval_status.value,
        }
        self._session.add(recommendation)
        self._session.commit()

        return RecommendationFeedbackResponse(
            feedback_id=feedback_id,
            memory_update_status=memory_status,
            eval_queue_status=eval_status,
            contradiction_alert=contradiction_alert,
        )

    def _apply_memory_update(
        self,
        *,
        recommendation: Recommendation,
        feedback_payload: JsonDict,
        structured_signal: JsonDict,
        recorded_at: dt.datetime,
    ) -> MemoryUpdateStatus:
        try:
            with self._session.begin_nested():
                observation = _memory_observation(
                    recommendation=recommendation,
                    feedback_payload=feedback_payload,
                    structured_signal=structured_signal,
                )
                summary = self._memory.latest_for_period(
                    user_id=recommendation.user_id,
                    period_type=PeriodType.daily,
                    start_date=recommendation.date,
                    end_date=recommendation.date,
                )
                if summary is None:
                    self._memory.create(
                        MemorySummary(
                            user_id=recommendation.user_id,
                            period_type=PeriodType.daily,
                            start_date=recommendation.date,
                            end_date=recommendation.date,
                            summary_version=FEEDBACK_MEMORY_VERSION,
                            observations=[observation],
                            hypotheses=[],
                            confidence=0.6,
                            source_refs=_unique_refs(observation["source_refs"]),
                            sensitive_fields_excluded=[
                                "recommendation_feedback.reason",
                                "recommendation_feedback.outcome_notes",
                            ],
                        )
                    )
                    return MemoryUpdateStatus.applied

                summary.observations = [
                    item
                    for item in summary.observations
                    if not _is_feedback_observation_for(item, recommendation.id)
                ]
                summary.observations.append(observation)
                summary.source_refs = _unique_refs(
                    [*summary.source_refs, *observation["source_refs"]]
                )
                summary.sensitive_fields_excluded = _unique_strings(
                    [
                        *summary.sensitive_fields_excluded,
                        "recommendation_feedback.reason",
                        "recommendation_feedback.outcome_notes",
                    ]
                )
                summary.confidence = _summary_confidence(summary.observations, summary.hypotheses)
                summary.updated_at = recorded_at
                self._session.add(summary)
                self._session.flush()
                return MemoryUpdateStatus.applied
        except Exception:
            return MemoryUpdateStatus.failed

    def _enqueue_eval(
        self,
        *,
        recommendation: Recommendation,
        feedback_payload: JsonDict,
        structured_signal: JsonDict,
        contradiction_alert: FeedbackContradictionAlert | None,
        memory_status: MemoryUpdateStatus,
        recorded_at: dt.datetime,
    ) -> EvalQueueStatus:
        if memory_status is not MemoryUpdateStatus.applied:
            return EvalQueueStatus.skipped

        try:
            with self._session.begin_nested():
                self._evals.create(
                    EvaluationCase(
                        scenario_name=FEEDBACK_EVAL_SCENARIO,
                        input_fixture={
                            "feedback_id": feedback_payload["feedback_id"],
                            "recommendation_id": str(recommendation.id),
                            "user_id": str(recommendation.user_id),
                            "date": recommendation.date.isoformat(),
                            "rating": feedback_payload["rating"],
                            "action_taken": feedback_payload["action_taken"],
                            "outcome_present": feedback_payload["outcome"]["notes_present"],
                            "wrong_because": structured_signal,
                        },
                        expected_properties={
                            "feedback_captured": True,
                            "outcome_linked_to_recommendation": True,
                            "safety_policy_mutation_allowed": SAFETY_POLICY_MUTATION_ALLOWED,
                        },
                        actual_output={
                            "memory_update_status": memory_status.value,
                            "eval_queue_status": EvalQueueStatus.queued.value,
                            "persistent_contradiction": contradiction_alert is not None,
                            "contradiction_key": (
                                contradiction_alert.contradiction_key
                                if contradiction_alert is not None
                                else None
                            ),
                        },
                        pass_fail=None,
                        failure_reason=None,
                        evaluated_at=recorded_at,
                    ),
                )
        except Exception:
            return EvalQueueStatus.failed
        return EvalQueueStatus.queued

    def _emit_audit(
        self,
        *,
        recommendation: Recommendation,
        feedback_id: UUID,
        feedback_payload: JsonDict,
        structured_signal: JsonDict,
        contradiction_alert: FeedbackContradictionAlert | None,
        recorded_at: dt.datetime,
    ) -> None:
        self._audits.create(
            AuditEvent(
                user_id=recommendation.user_id,
                event_type=AuditEventType.feedback_submitted,
                actor="user",
                timestamp=recorded_at,
                event_metadata={
                    "feedback_id": str(feedback_id),
                    "recommendation_id": str(recommendation.id),
                    "rating": feedback_payload["rating"],
                    "action_taken": feedback_payload["action_taken"],
                    "wrong_because_categories": structured_signal["categories"],
                    "outcome_notes_present": feedback_payload["outcome"]["notes_present"],
                    "persistent_contradiction": contradiction_alert is not None,
                    "safety_policy_mutation_allowed": SAFETY_POLICY_MUTATION_ALLOWED,
                },
                redaction_status=RedactionStatus.redacted,
            )
        )

    def _contradiction_alert(
        self,
        *,
        recommendation: Recommendation,
        structured_signal: JsonDict,
    ) -> FeedbackContradictionAlert | None:
        contradiction_key = structured_signal["contradiction_key"]
        if contradiction_key is None:
            return None

        previous_count = 0
        statement = select(Recommendation).where(
            Recommendation.user_id == recommendation.user_id,
            Recommendation.id != recommendation.id,
            col(Recommendation.user_feedback).is_not(None),
        )
        for row in self._session.exec(statement).all():
            feedback = row.user_feedback
            if not isinstance(feedback, Mapping):
                continue
            wrong_because = feedback.get("wrong_because")
            if not isinstance(wrong_because, Mapping):
                continue
            if wrong_because.get("contradiction_key") == contradiction_key:
                previous_count += 1

        total_count = previous_count + 1
        if total_count < _CONTRADICTION_THRESHOLD:
            return None
        return FeedbackContradictionAlert(
            contradiction_key=contradiction_key,
            count=total_count,
            message=(
                "Repeated feedback contradicts current reasoning; surface for review "
                "instead of silently changing safety or reasoning rules."
            ),
        )


def _feedback_payload(
    *,
    feedback_id: UUID,
    recommendation: Recommendation,
    request: RecommendationFeedbackRequest,
    structured_signal: JsonDict,
    contradiction_alert: FeedbackContradictionAlert | None,
    recorded_at: dt.datetime,
) -> JsonDict:
    return {
        "feedback_id": str(feedback_id),
        "recommendation_id": str(recommendation.id),
        "rating": request.rating.value,
        "action_taken": request.action_taken.value,
        "reason": _redacted_text_signal(request.reason),
        "outcome_notes": _redacted_text_signal(request.outcome_notes),
        "wrong_because": structured_signal,
        "outcome": {
            "notes_present": request.outcome_notes is not None,
            "notes_length_bucket": _text_length_bucket(request.outcome_notes),
            "notes_redaction_status": (
                RedactionStatus.redacted.value
                if request.outcome_notes is not None
                else RedactionStatus.none.value
            ),
            "linked_recommendation_id": str(recommendation.id),
            "recommendation_date": recommendation.date.isoformat(),
            "captured_at": recorded_at.isoformat(),
        },
        "contradiction_alert": (
            contradiction_alert.model_dump(mode="json") if contradiction_alert is not None else None
        ),
        "safety_policy_mutation_allowed": SAFETY_POLICY_MUTATION_ALLOWED,
        "recorded_at": recorded_at.isoformat(),
    }


def _accepted_action_payload(
    *,
    feedback_id: UUID,
    recommendation_id: UUID,
    action_taken: str,
    recorded_at: dt.datetime,
) -> JsonDict:
    return {
        "feedback_id": str(feedback_id),
        "recommendation_id": str(recommendation_id),
        "action_taken": action_taken,
        "recorded_at": recorded_at.isoformat(),
    }


def _structured_wrong_because(
    request: RecommendationFeedbackRequest,
    recommendation: Recommendation,
) -> JsonDict:
    reason = request.reason or ""
    categories = _reason_categories(reason)
    is_wrong_signal = request.rating in {
        FeedbackRating.not_useful,
        FeedbackRating.unsafe_or_wrong,
    }
    contradiction_key = _contradiction_key(
        categories=categories,
        recommendation_band=_recommendation_band(recommendation),
        is_wrong_signal=is_wrong_signal,
    )
    return {
        "present": bool(reason) and is_wrong_signal,
        "reason_present": bool(reason) and is_wrong_signal,
        "reason_length_bucket": _text_length_bucket(reason if is_wrong_signal else None),
        "reason_redaction_status": (
            RedactionStatus.redacted.value
            if reason and is_wrong_signal
            else RedactionStatus.none.value
        ),
        "categories": categories if is_wrong_signal else [],
        "contradiction_key": contradiction_key,
        "contradicts_current_reasoning": contradiction_key is not None,
    }


def _reason_categories(reason: str) -> list[str]:
    normalized = reason.lower()
    categories: list[str] = []
    if any(term in normalized for term in ("felt great", "ready", "too conservative", "easy")):
        categories.append("user_reported_readiness_higher_than_reasoning")
    if any(term in normalized for term in ("exhausted", "tired", "sore", "too hard", "wrecked")):
        categories.append("user_reported_readiness_lower_than_reasoning")
    if any(term in normalized for term in ("unsafe", "pain", "injury", "illness")):
        categories.append("safety_concern_reported")
    if not categories and reason:
        categories.append("reasoning_disagreement")
    return _unique_strings(categories)


def _contradiction_key(
    *,
    categories: list[str],
    recommendation_band: RecommendationBand | None,
    is_wrong_signal: bool,
) -> str | None:
    if not is_wrong_signal:
        return None
    if (
        "user_reported_readiness_higher_than_reasoning" in categories
        and recommendation_band in _CONSERVATIVE_BANDS
    ):
        return "conservative_recommendation_user_felt_ready"
    if (
        "user_reported_readiness_lower_than_reasoning" in categories
        and recommendation_band in _AGGRESSIVE_BANDS
    ):
        return "aggressive_recommendation_user_felt_underrecovered"
    if "safety_concern_reported" in categories:
        return "safety_concern_reported"
    return None


def _redacted_text_signal(value: str | None) -> JsonDict:
    return {
        "present": value is not None,
        "length_bucket": _text_length_bucket(value),
        "redaction_status": (
            RedactionStatus.redacted.value if value is not None else RedactionStatus.none.value
        ),
    }


def _text_length_bucket(value: str | None) -> str:
    if value is None:
        return "none"
    length = len(value)
    if length <= 80:
        return "short"
    if length <= 160:
        return "medium"
    return "long"


def _recommendation_band(recommendation: Recommendation) -> RecommendationBand | None:
    raw_band = recommendation.briefing_payload.get("recommendation_band")
    if not isinstance(raw_band, str):
        return None
    try:
        return RecommendationBand(raw_band)
    except ValueError:
        return None


def _memory_observation(
    *,
    recommendation: Recommendation,
    feedback_payload: JsonDict,
    structured_signal: JsonDict,
) -> JsonDict:
    source_refs = [
        {
            "table": "recommendation",
            "id": str(recommendation.id),
            "field": "user_feedback",
        },
        {
            "table": "recommendation",
            "id": str(recommendation.id),
            "field": "accepted_action",
        },
    ]
    return {
        "kind": "observation",
        "key": "recommendation_feedback",
        "text": "Recommendation feedback captured and linked to outcome signals.",
        "value": {
            "feedback_id": feedback_payload["feedback_id"],
            "rating": feedback_payload["rating"],
            "action_taken": feedback_payload["action_taken"],
            "outcome_notes_present": feedback_payload["outcome"]["notes_present"],
            "wrong_because_present": structured_signal["present"],
            "wrong_because_categories": structured_signal["categories"],
            "contradiction_key": structured_signal["contradiction_key"],
        },
        "confidence": 0.6,
        "source_refs": source_refs,
    }


def _is_feedback_observation_for(item: Mapping[str, Any], recommendation_id: UUID) -> bool:
    if item.get("key") != "recommendation_feedback":
        return False
    source_refs = item.get("source_refs")
    if not isinstance(source_refs, list):
        return False
    return any(
        isinstance(ref, Mapping)
        and ref.get("table") == "recommendation"
        and ref.get("id") == str(recommendation_id)
        for ref in source_refs
    )


def _summary_confidence(observations: list[JsonDict], hypotheses: list[JsonDict]) -> float:
    values = [
        float(item["confidence"])
        for item in [*observations, *hypotheses]
        if isinstance(item.get("confidence"), int | float)
    ]
    if not values:
        return 0.0
    return round(max(0.0, min(1.0, sum(values) / len(values))), 3)


def _unique_refs(refs: list[Mapping[str, Any]]) -> list[JsonDict]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    unique: list[JsonDict] = []
    for ref in refs:
        normalized = {str(key): str(value) for key, value in ref.items()}
        key = tuple(sorted(normalized.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
