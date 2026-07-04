"""Deterministic readiness reasoning tests."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlmodel import select

from baseline_api.db.models import DerivedDailyFeature, ReadinessAssessment, ReasoningTrace, User
from baseline_api.db.models.enums import (
    ConfidenceLevel,
    PrivacyMode,
    ReadinessState,
    RecommendationBand,
)
from baseline_api.reasoning.engine import (
    BAND_RANK,
    RISK_FLAG_BAND_CEILINGS,
    ReasoningInput,
    assess_readiness,
)
from baseline_api.reasoning.service import ReasoningService


def _value(value: Any, unit: str = "unit") -> dict[str, Any]:
    return {"status": "computed", "value": value, "unit": unit}


def _features(
    *,
    sleep_debt: float = 0.3,
    hrv_pct: float = 6.0,
    rhr_pct: float = -1.0,
    rhr_bpm: float = -1.0,
    load_balance: str = "balanced",
    density_sessions: int = 1,
    completeness: float = 1.0,
    flags: list[str] | None = None,
) -> dict[str, Any]:
    quality_flags = flags or []
    return {
        "feature_version": "test-v1",
        "sleep_features": {
            "values": {"sleep_debt_hours": _value(sleep_debt, "h")},
            "data_quality": {"flags": quality_flags, "completeness": 1.0},
        },
        "hrv_features": {
            "values": {"deviation_pct": _value(hrv_pct, "percent")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        "rhr_features": {
            "values": {
                "deviation_pct": _value(rhr_pct, "percent"),
                "deviation_bpm": _value(rhr_bpm, "bpm"),
            },
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        "training_load_features": {
            "values": {
                "acute_chronic_ratio": _value(1.0, "ratio"),
                "load_balance": _value(load_balance, "category"),
                "density_by_modality": _value(
                    {
                        "run": {
                            "status": "computed",
                            "value": density_sessions,
                            "unit": "sessions",
                        }
                    },
                    "structured",
                ),
            },
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        "recovery_features": {
            "values": {"level": _value("high", "category")},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        "goal_features": {
            "values": {},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        "data_quality": {
            "flags": quality_flags,
            "overall_completeness": completeness,
            "section_completeness": {},
        },
        "anomaly_flags": quality_flags,
    }


def _input(
    *,
    features: Mapping[str, Any] | None = None,
    check_in: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    goals: list[dict[str, Any]] | None = None,
) -> ReasoningInput:
    return ReasoningInput(
        target_date=dt.date(2026, 7, 4),
        features=features or _features(),
        daily_check_in=check_in
        if check_in is not None
        else {
            "energy_score": 6,
            "soreness_score": 2,
            "perceived_recovery_score": 8,
            "illness_flag": False,
            "injury_flag": False,
            "travel_flag": False,
        },
        user_constraints=constraints or {},
        active_goals=goals or [],
        recent_memory=[],
    )


def _feature_row(user_id: UUID, *, date: dt.date = dt.date(2026, 7, 4)) -> DerivedDailyFeature:
    features = _features()
    return DerivedDailyFeature(
        user_id=user_id,
        date=date,
        feature_version=str(features["feature_version"]),
        sleep_features=features["sleep_features"],
        hrv_features=features["hrv_features"],
        rhr_features=features["rhr_features"],
        training_load_features=features["training_load_features"],
        recovery_features=features["recovery_features"],
        goal_features=features["goal_features"],
        data_quality=features["data_quality"],
        anomaly_flags=features["anomaly_flags"],
        computed_at=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
        source_sample_ids=[],
    )


@given(
    sleep_debt=st.floats(min_value=0, max_value=4, allow_nan=False, allow_infinity=False),
    hrv_pct=st.floats(min_value=-25, max_value=20, allow_nan=False, allow_infinity=False),
    rhr_pct=st.floats(min_value=-10, max_value=15, allow_nan=False, allow_infinity=False),
    density=st.integers(min_value=0, max_value=5),
)
def test_assessment_mandatory_fields_are_always_present(
    sleep_debt: float,
    hrv_pct: float,
    rhr_pct: float,
    density: int,
) -> None:
    result = assess_readiness(
        _input(
            features=_features(
                sleep_debt=sleep_debt,
                hrv_pct=hrv_pct,
                rhr_pct=rhr_pct,
                rhr_bpm=rhr_pct / 2,
                density_sessions=density,
            )
        )
    )

    assert isinstance(result.readiness_state, ReadinessState)
    assert isinstance(result.recommendation_band, RecommendationBand)
    assert isinstance(result.confidence, ConfidenceLevel)
    assert result.evidence_items
    assert result.uncertainty
    assert result.risk_flags is not None
    assert result.reasoning_trace_id
    assert result.reasoning_trace["rules_fired"] is not None


def test_bad_data_low_readiness_is_distinguished_from_unfavorable_physiology() -> None:
    result = assess_readiness(
        _input(
            features=_features(
                completeness=0.2,
                flags=["missing_sleep", "stale_heart_rate_variability"],
            )
        )
    )

    assert result.readiness_state == ReadinessState.insufficient_data
    assert result.recommendation_band == RecommendationBand.insufficient_data
    assert "data_quality_low_readiness" in result.risk_flags
    assert "physiology_low_readiness" not in result.risk_flags
    assert result.reasoning_trace["readiness_basis"] == "data_quality"


def test_unfavorable_physiology_low_readiness_is_not_bad_data() -> None:
    result = assess_readiness(
        _input(
            features=_features(sleep_debt=3.0, hrv_pct=-14.0, rhr_pct=10.0, rhr_bpm=7.0),
            check_in={
                "energy_score": 3,
                "soreness_score": 8,
                "perceived_recovery_score": 3,
                "illness_flag": False,
                "injury_flag": False,
                "travel_flag": False,
            },
        )
    )

    assert result.readiness_state == ReadinessState.low
    assert "physiology_low_readiness" in result.risk_flags
    assert "data_quality_low_readiness" not in result.risk_flags
    assert result.reasoning_trace["readiness_basis"] == "physiology"


def test_conflict_detection_emits_multiple_options() -> None:
    result = assess_readiness(
        _input(
            features=_features(sleep_debt=2.5, hrv_pct=-12.0),
            constraints={"motivation_score": 9, "intended_intensity": "hard"},
        )
    )

    assert "conflicting_signals" in result.risk_flags
    assert result.readiness_state == ReadinessState.mixed
    assert len(result.candidate_options) >= 2
    assert result.confidence != ConfidenceLevel.high


def test_goal_tradeoffs_are_computed_from_active_goal_set() -> None:
    result = assess_readiness(
        _input(
            features=_features(sleep_debt=2.5),
            goals=[
                {"category": "vo2_max", "priority": 2},
                {"category": "sleep", "priority": 1},
            ],
        )
    )

    assert [tradeoff["goal"] for tradeoff in result.goal_tradeoffs] == ["vo2_max", "sleep"]
    assert all(tradeoff["tradeoff"] for tradeoff in result.goal_tradeoffs)


def test_assessment_and_trace_are_deterministic() -> None:
    payload = _input(features=_features(sleep_debt=1.2, hrv_pct=7.0, density_sessions=2))

    first = assess_readiness(payload)
    second = assess_readiness(payload)

    assert first == second
    assert first.reasoning_trace_id == second.reasoning_trace_id
    assert first.reasoning_trace == second.reasoning_trace


def test_conservative_default_triggered_by_each_risk_flag() -> None:
    cases = {
        "hard_safety_illness": _input(check_in={"illness_flag": True}),
        "hard_safety_injury": _input(check_in={"injury_flag": True}),
        "hard_safety_medical_boundary": _input(
            constraints={"user_request": "Can you diagnose why my resting heart rate is high?"}
        ),
        "missing_or_stale_data": _input(
            features=_features(flags=["missing_heart_rate_variability"])
        ),
        "elevated_rhr": _input(features=_features(rhr_pct=10, rhr_bpm=7)),
        "high_sleep_debt": _input(features=_features(sleep_debt=2.5)),
        "high_training_density": _input(features=_features(density_sessions=3)),
        "poor_subjective_recovery": _input(
            check_in={"perceived_recovery_score": 3, "illness_flag": False, "injury_flag": False}
        ),
        "high_soreness": _input(
            check_in={"soreness_score": 8, "illness_flag": False, "injury_flag": False}
        ),
        "conflicting_signals": _input(
            features=_features(sleep_debt=2.5),
            constraints={"motivation_score": 9},
        ),
        "data_quality_low_readiness": _input(
            features=_features(completeness=0.2, flags=["missing_sleep"])
        ),
    }

    assert set(cases) == set(RISK_FLAG_BAND_CEILINGS)
    for risk_flag, reasoning_input in cases.items():
        result = assess_readiness(reasoning_input)
        assert risk_flag in result.risk_flags
        assert (
            BAND_RANK[result.recommendation_band] <= BAND_RANK[RISK_FLAG_BAND_CEILINGS[risk_flag]]
        )


def test_medical_diagnosis_request_routes_to_safety_not_training_band() -> None:
    result = assess_readiness(
        _input(constraints={"user_request": "Can you diagnose why my resting heart rate is high?"})
    )

    assert result.reasoning_trace["request_route"] == "blocked_or_redirected"
    assert "hard_safety_medical_boundary" in result.risk_flags
    assert "medical_boundary" in result.hard_safety_flags
    assert result.recommendation_band == RecommendationBand.rest


def test_reasoning_service_rejects_user_feature_mismatch(db_session) -> None:
    owner = User(privacy_mode=PrivacyMode.local_only, active_consent_version="v1")
    db_session.add(owner)
    db_session.flush()

    with pytest.raises(ValueError, match="user_id must match"):
        ReasoningService(db_session).assess_and_persist(
            user_id=uuid4(),
            derived_features=_feature_row(owner.id),
        )

    assert db_session.exec(select(ReasoningTrace)).first() is None
    assert db_session.exec(select(ReadinessAssessment)).first() is None


def test_reasoning_service_rerun_is_idempotent_for_same_input(db_session) -> None:
    user = User(privacy_mode=PrivacyMode.local_only, active_consent_version="v1")
    db_session.add(user)
    db_session.flush()

    service = ReasoningService(db_session)
    features = _feature_row(user.id)

    first = service.assess_and_persist(user_id=user.id, derived_features=features)
    second = service.assess_and_persist(user_id=user.id, derived_features=features)

    assert second == first

    traces = list(db_session.exec(select(ReasoningTrace)).all())
    assessments = list(db_session.exec(select(ReadinessAssessment)).all())
    assert len(traces) == 1
    assert len(assessments) == 1
    assert traces[0].id == first.reasoning_trace_id
    assert assessments[0].reasoning_trace_id == first.reasoning_trace_id
    assert assessments[0].user_id == user.id
