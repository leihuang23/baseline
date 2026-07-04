"""Persistence service for deterministic readiness assessments."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlmodel import Session

from baseline_api.db.models.assessment import ReadinessAssessment, ReasoningTrace
from baseline_api.db.models.features import DerivedDailyFeature
from baseline_api.db.repositories.assessment import (
    ReadinessAssessmentRepository,
    ReasoningTraceRepository,
)
from baseline_api.reasoning.engine import (
    ReadinessAssessmentOutput,
    ReasoningInput,
    assess_readiness,
)


class ReasoningService:
    """Run and persist the deterministic readiness assessment."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._assessments = ReadinessAssessmentRepository(session)
        self._traces = ReasoningTraceRepository(session)

    def assess_and_persist(
        self,
        *,
        user_id: UUID,
        derived_features: DerivedDailyFeature,
        active_goals: Sequence[Any] = (),
        recent_memory: Sequence[Any] = (),
        user_constraints: Mapping[str, Any] | None = None,
        daily_check_in: Mapping[str, Any] | None = None,
        include_external_knowledge: bool = False,
    ) -> ReadinessAssessmentOutput:
        """Run pure reasoning and persist assessment plus trace in one transaction."""

        if user_id != derived_features.user_id:
            raise ValueError("user_id must match derived_features.user_id")

        result = assess_readiness(
            ReasoningInput(
                target_date=derived_features.date,
                features=features_to_mapping(derived_features),
                active_goals=active_goals,
                recent_memory=recent_memory,
                user_constraints=user_constraints or {},
                daily_check_in=daily_check_in,
                include_external_knowledge=include_external_knowledge,
            )
        )
        self._persist_trace(user_id=user_id, derived_features=derived_features, result=result)
        self._persist_assessment(user_id=user_id, derived_features=derived_features, result=result)
        self._session.commit()
        return result

    def _persist_trace(
        self,
        *,
        user_id: UUID,
        derived_features: DerivedDailyFeature,
        result: ReadinessAssessmentOutput,
    ) -> None:
        input_hash = str(result.reasoning_trace["inputs_hash"])
        existing = self._traces.get_by_id(result.reasoning_trace_id)
        if existing is not None:
            if existing.user_id != user_id:
                raise ValueError("reasoning trace is already owned by a different user")
            if existing.date != derived_features.date or existing.input_hash != input_hash:
                raise ValueError("reasoning trace id collides with different assessment inputs")
            return

        self._traces.create(
            ReasoningTrace(
                id=result.reasoning_trace_id,
                user_id=user_id,
                date=derived_features.date,
                trace_version=result.assessment_version,
                assessment_version=result.assessment_version,
                input_hash=input_hash,
                rules_fired=list(result.reasoning_trace["rules_fired"]),
                hard_safety_flags=result.hard_safety_flags,
                trace_payload=result.reasoning_trace,
            )
        )

    def _persist_assessment(
        self,
        *,
        user_id: UUID,
        derived_features: DerivedDailyFeature,
        result: ReadinessAssessmentOutput,
    ) -> None:
        existing = self._assessments.get_by_user_date_trace(
            user_id=user_id,
            date=derived_features.date,
            reasoning_trace_id=result.reasoning_trace_id,
        )
        if existing is None:
            existing = ReadinessAssessment(
                user_id=user_id,
                date=derived_features.date,
                assessment_version=result.assessment_version,
                readiness_state=result.readiness_state,
                recommendation_band=result.recommendation_band,
                confidence=result.confidence,
                uncertainty=result.uncertainty,
                evidence_items=result.evidence_items,
                risk_flags=result.risk_flags,
                candidate_options=result.candidate_options,
                follow_up_questions=result.follow_up_questions,
                goal_tradeoffs=result.goal_tradeoffs,
                hard_safety_flags=result.hard_safety_flags,
                reasoning_trace_id=result.reasoning_trace_id,
            )
            self._assessments.create(existing)
            return

        existing.assessment_version = result.assessment_version
        existing.readiness_state = result.readiness_state
        existing.recommendation_band = result.recommendation_band
        existing.confidence = result.confidence
        existing.uncertainty = result.uncertainty
        existing.evidence_items = result.evidence_items
        existing.risk_flags = result.risk_flags
        existing.candidate_options = result.candidate_options
        existing.follow_up_questions = result.follow_up_questions
        existing.goal_tradeoffs = result.goal_tradeoffs
        existing.hard_safety_flags = result.hard_safety_flags
        self._session.add(existing)


def features_to_mapping(derived_features: DerivedDailyFeature) -> dict[str, Any]:
    """Project a DerivedDailyFeature ORM row into the pure engine input shape."""

    return {
        "feature_version": derived_features.feature_version,
        "sleep_features": derived_features.sleep_features,
        "hrv_features": derived_features.hrv_features,
        "rhr_features": derived_features.rhr_features,
        "training_load_features": derived_features.training_load_features,
        "recovery_features": derived_features.recovery_features,
        "goal_features": derived_features.goal_features,
        "data_quality": derived_features.data_quality,
        "anomaly_flags": derived_features.anomaly_flags,
        "source_sample_ids": derived_features.source_sample_ids,
        "computed_at": _iso_datetime(derived_features.computed_at),
    }


def _iso_datetime(value: dt.datetime) -> str:
    return value.isoformat()
