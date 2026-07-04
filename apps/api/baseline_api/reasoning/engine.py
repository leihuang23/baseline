"""Pure deterministic readiness reasoning rules.

The LLM explanation layer may describe these outputs, but it must not override
hard safety flags emitted here.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid5

from baseline_api.db.models.enums import ConfidenceLevel, ReadinessState, RecommendationBand

ASSESSMENT_VERSION = "p3-02-v1"
TRACE_NAMESPACE = UUID("9bc0b38b-6f31-5cb5-935b-1f67573a7b21")
REQUEST_ROUTE_TRAINING = "training_reasoning"
REQUEST_ROUTE_SAFETY_REDIRECT = "blocked_or_redirected"

JsonDict = dict[str, Any]

BAND_RANK: dict[RecommendationBand, int] = {
    RecommendationBand.insufficient_data: 0,
    RecommendationBand.rest: 1,
    RecommendationBand.recovery: 2,
    RecommendationBand.easy_or_recovery: 3,
    RecommendationBand.easy: 4,
    RecommendationBand.moderate_or_upper_body: 5,
    RecommendationBand.moderate: 6,
    RecommendationBand.hard_training_ok: 7,
}

RISK_FLAG_BAND_CEILINGS: dict[str, RecommendationBand] = {
    "hard_safety_illness": RecommendationBand.rest,
    "hard_safety_injury": RecommendationBand.rest,
    "hard_safety_medical_boundary": RecommendationBand.rest,
    "missing_or_stale_data": RecommendationBand.moderate,
    "elevated_rhr": RecommendationBand.easy_or_recovery,
    "high_sleep_debt": RecommendationBand.easy_or_recovery,
    "high_training_density": RecommendationBand.moderate_or_upper_body,
    "poor_subjective_recovery": RecommendationBand.recovery,
    "high_soreness": RecommendationBand.recovery,
    "conflicting_signals": RecommendationBand.easy,
    "data_quality_low_readiness": RecommendationBand.insufficient_data,
}

THRESHOLDS: JsonDict = {
    "minimum_usable_completeness": 0.45,
    "high_sleep_debt_hours": 2.0,
    "moderate_sleep_debt_hours": 1.0,
    "elevated_rhr_pct": 8.0,
    "elevated_rhr_bpm": 5.0,
    "unfavorable_hrv_pct": -10.0,
    "favorable_hrv_pct": 5.0,
    "high_density_sessions": 3,
    "high_soreness_score": 7,
    "poor_recovery_score": 4,
    "high_motivation_score": 8,
}


@dataclass(frozen=True, slots=True)
class ReasoningInput:
    """JSON-shaped inputs consumed by the deterministic reasoner."""

    target_date: dt.date
    features: Mapping[str, Any]
    active_goals: Sequence[Any] = ()
    recent_memory: Sequence[Any] = ()
    user_constraints: Mapping[str, Any] = field(default_factory=dict)
    daily_check_in: Mapping[str, Any] | None = None
    include_external_knowledge: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessAssessmentOutput:
    """Complete deterministic reasoning output before LLM explanation."""

    assessment_version: str
    readiness_state: ReadinessState
    evidence_items: list[JsonDict]
    risk_flags: list[str]
    recommendation_band: RecommendationBand
    confidence: ConfidenceLevel
    uncertainty: list[str]
    follow_up_questions: list[JsonDict]
    goal_tradeoffs: list[JsonDict]
    candidate_options: list[JsonDict]
    hard_safety_flags: list[str]
    reasoning_trace_id: UUID
    reasoning_trace: JsonDict


@dataclass(slots=True)
class _SignalState:
    risk_flags: list[str] = field(default_factory=list)
    hard_safety_flags: list[str] = field(default_factory=list)
    evidence_items: list[JsonDict] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    follow_up_questions: list[JsonDict] = field(default_factory=list)
    rules_fired: list[JsonDict] = field(default_factory=list)
    favorable_signals: list[str] = field(default_factory=list)
    unfavorable_signals: list[str] = field(default_factory=list)
    confidence_reductions: int = 0


def assess_readiness(reasoning_input: ReasoningInput) -> ReadinessAssessmentOutput:
    """Assess readiness with deterministic rules and a canonical trace."""

    state = _SignalState()
    features = reasoning_input.features
    flags = _quality_flags(features)
    data_quality = _data_quality(features)
    completeness = _numeric(data_quality.get("overall_completeness"))

    _add_evidence(
        state,
        metric="data_quality.overall_completeness",
        value=completeness if completeness is not None else "unknown",
        interpretation="usable" if completeness is None or completeness >= 0.8 else "degraded",
        source="derived_daily_feature.data_quality",
    )

    _apply_data_quality_rules(state, flags, completeness)
    _apply_sleep_rules(state, features)
    _apply_cardio_rules(state, features)
    _apply_training_load_rules(state, features)
    _apply_check_in_rules(state, reasoning_input.daily_check_in)
    _apply_constraint_rules(state, reasoning_input.user_constraints)
    request_route = _apply_request_safety_rules(state, reasoning_input.user_constraints)
    _apply_memory_rules(state, reasoning_input.recent_memory)
    _apply_external_knowledge_rule(state, reasoning_input.include_external_knowledge)
    _apply_conflict_rules(state, reasoning_input)

    readiness_state, readiness_basis = _readiness_state(state, completeness)
    recommendation_band = _recommendation_band(readiness_state, state)
    confidence = _confidence_level(state, completeness)
    uncertainty = state.uncertainty or [
        "No material uncertainty beyond normal day-to-day variability."
    ]
    follow_ups = state.follow_up_questions
    goal_tradeoffs = _goal_tradeoffs(reasoning_input.active_goals, state, recommendation_band)
    candidate_options = _candidate_options(recommendation_band, state, confidence)

    trace_payload = {
        "assessment_version": ASSESSMENT_VERSION,
        "target_date": reasoning_input.target_date.isoformat(),
        "inputs_hash": _hash_json(_canonical_input_payload(reasoning_input)),
        "readiness_basis": readiness_basis,
        "thresholds": THRESHOLDS,
        "rules_fired": state.rules_fired,
        "risk_flags": _unique(state.risk_flags),
        "hard_safety_flags": _unique(state.hard_safety_flags),
        "request_route": request_route,
        "confidence_reductions": state.confidence_reductions,
        "recommendation_band": recommendation_band.value,
        "candidate_options": candidate_options,
    }
    trace_id = uuid5(TRACE_NAMESPACE, _canonical_json(trace_payload))
    trace_payload["reasoning_trace_id"] = str(trace_id)

    return ReadinessAssessmentOutput(
        assessment_version=ASSESSMENT_VERSION,
        readiness_state=readiness_state,
        evidence_items=state.evidence_items,
        risk_flags=_unique(state.risk_flags),
        recommendation_band=recommendation_band,
        confidence=confidence,
        uncertainty=uncertainty,
        follow_up_questions=follow_ups,
        goal_tradeoffs=goal_tradeoffs,
        candidate_options=candidate_options,
        hard_safety_flags=_unique(state.hard_safety_flags),
        reasoning_trace_id=trace_id,
        reasoning_trace=trace_payload,
    )


def _apply_data_quality_rules(
    state: _SignalState,
    flags: list[str],
    completeness: float | None,
) -> None:
    if completeness is not None and completeness < THRESHOLDS["minimum_usable_completeness"]:
        _fire(
            state,
            "data_quality_low_readiness",
            evidence={"overall_completeness": completeness},
            risk_flag="data_quality_low_readiness",
            uncertainty="Readiness is limited by incomplete or unreliable input data.",
            confidence_reduction=True,
        )
    if any(flag.startswith("missing_") or flag.startswith("stale_") for flag in flags):
        sources = sorted(flag for flag in flags if flag.startswith(("missing_", "stale_")))
        _fire(
            state,
            "missing_or_stale_data",
            evidence={"flags": sources},
            risk_flag="missing_or_stale_data",
            uncertainty=f"Missing or stale inputs reduce confidence: {', '.join(sources)}.",
            confidence_reduction=True,
        )
    if any(flag.startswith("baseline_not_established") for flag in flags):
        _fire(
            state,
            "baseline_not_established",
            evidence={"flags": sorted(flag for flag in flags if flag.startswith("baseline_"))},
            uncertainty="Some baseline comparisons are early or not yet established.",
            confidence_reduction=True,
        )
    if any(flag.startswith(("conflicting_", "anomalous_")) for flag in flags):
        _fire(
            state,
            "input_quality_conflict_or_anomaly",
            evidence={
                "flags": sorted(
                    flag for flag in flags if flag.startswith(("conflicting_", "anomalous_"))
                )
            },
            risk_flag="conflicting_signals",
            uncertainty=(
                "Conflicting or anomalous inputs make the safest interpretation less certain."
            ),
            confidence_reduction=True,
        )


def _apply_sleep_rules(state: _SignalState, features: Mapping[str, Any]) -> None:
    debt = _feature_value(features, "sleep_features", "sleep_debt_hours")
    if debt is None:
        _fire(
            state,
            "sleep_data_missing",
            uncertainty="Sleep-specific certainty is limited because sleep debt was not computed.",
            confidence_reduction=True,
        )
        _follow_up(
            state,
            "How much and how well did you sleep last night?",
            "Sleep data was missing or incomplete.",
        )
        return

    interpretation = (
        "favorable" if debt < THRESHOLDS["moderate_sleep_debt_hours"] else "unfavorable"
    )
    _add_evidence(
        state,
        metric="sleep_debt_hours",
        value=debt,
        interpretation=interpretation,
        source="sleep_features.values.sleep_debt_hours",
    )
    if debt >= THRESHOLDS["high_sleep_debt_hours"]:
        _fire(
            state,
            "high_sleep_debt",
            evidence={"sleep_debt_hours": debt},
            risk_flag="high_sleep_debt",
            unfavorable_signal="sleep_debt",
            confidence_reduction=True,
        )
    elif debt >= THRESHOLDS["moderate_sleep_debt_hours"]:
        state.unfavorable_signals.append("moderate_sleep_debt")
    else:
        state.favorable_signals.append("low_sleep_debt")


def _apply_cardio_rules(state: _SignalState, features: Mapping[str, Any]) -> None:
    hrv_pct = _feature_value(features, "hrv_features", "deviation_pct")
    if hrv_pct is not None:
        interpretation = "favorable" if hrv_pct >= 0 else "unfavorable"
        _add_evidence(
            state,
            metric="hrv_deviation_pct",
            value=hrv_pct,
            interpretation=interpretation,
            source="hrv_features.values.deviation_pct",
        )
        if hrv_pct <= THRESHOLDS["unfavorable_hrv_pct"]:
            state.unfavorable_signals.append("unfavorable_hrv")
        elif hrv_pct >= THRESHOLDS["favorable_hrv_pct"]:
            state.favorable_signals.append("favorable_hrv")

    rhr_pct = _feature_value(features, "rhr_features", "deviation_pct")
    rhr_bpm = _feature_value(features, "rhr_features", "deviation_bpm")
    if rhr_pct is not None:
        _add_evidence(
            state,
            metric="rhr_deviation_pct",
            value=rhr_pct,
            interpretation="unfavorable" if rhr_pct > 0 else "favorable",
            source="rhr_features.values.deviation_pct",
        )
    if _is_elevated_rhr(rhr_pct, rhr_bpm):
        _fire(
            state,
            "elevated_rhr",
            evidence={"rhr_deviation_pct": rhr_pct, "rhr_deviation_bpm": rhr_bpm},
            risk_flag="elevated_rhr",
            unfavorable_signal="elevated_rhr",
            confidence_reduction=True,
        )


def _apply_training_load_rules(state: _SignalState, features: Mapping[str, Any]) -> None:
    load_balance = _feature_value(features, "training_load_features", "load_balance")
    acute_chronic = _feature_value(features, "training_load_features", "acute_chronic_ratio")
    modality_density = _feature_value(features, "training_load_features", "density_by_modality")
    muscle_group_density = _feature_value(
        features, "training_load_features", "density_by_muscle_group"
    )
    max_density = max(
        _max_density_count(modality_density),
        _max_density_count(muscle_group_density),
    )

    if acute_chronic is not None:
        _add_evidence(
            state,
            metric="acute_chronic_ratio",
            value=acute_chronic,
            interpretation="unfavorable" if acute_chronic >= 1.3 else "usable",
            source="training_load_features.values.acute_chronic_ratio",
        )
    if (
        load_balance in {"elevated", "high_spike"}
        or max_density >= THRESHOLDS["high_density_sessions"]
    ):
        _fire(
            state,
            "high_training_density",
            evidence={"load_balance": load_balance, "max_density_sessions": max_density},
            risk_flag="high_training_density",
            unfavorable_signal="training_density",
            confidence_reduction=load_balance == "high_spike",
        )


def _apply_check_in_rules(
    state: _SignalState,
    daily_check_in: Mapping[str, Any] | None,
) -> None:
    if daily_check_in is None:
        _fire(
            state,
            "manual_check_in_absent",
            uncertainty="Manual check-in is absent, so subjective recovery context is unknown.",
            confidence_reduction=True,
        )
        _follow_up(
            state,
            "What are your energy, soreness, stress, and perceived recovery scores today?",
            "Subjective context can change the safest training band.",
        )
        return

    if _truthy(daily_check_in.get("illness_flag")):
        _fire(
            state,
            "hard_safety_illness",
            risk_flag="hard_safety_illness",
            hard_safety_flag="illness",
            unfavorable_signal="illness",
            uncertainty="Recent illness can make normal physiological baselines less reliable.",
            confidence_reduction=True,
        )
    if _truthy(daily_check_in.get("injury_flag")):
        _fire(
            state,
            "hard_safety_injury",
            risk_flag="hard_safety_injury",
            hard_safety_flag="injury",
            unfavorable_signal="injury",
            uncertainty="Reported injury requires the lowest-risk recommendation band.",
            confidence_reduction=True,
        )
    if _truthy(daily_check_in.get("travel_flag")):
        _fire(
            state,
            "recent_travel",
            uncertainty="Recent travel may temporarily distort recovery signals.",
            confidence_reduction=True,
        )

    soreness = _numeric(daily_check_in.get("soreness_score"))
    recovery = _numeric(daily_check_in.get("perceived_recovery_score"))
    energy = _numeric(daily_check_in.get("energy_score"))
    if soreness is not None:
        _add_evidence(
            state,
            metric="soreness_score",
            value=soreness,
            interpretation=(
                "unfavorable" if soreness >= THRESHOLDS["high_soreness_score"] else "usable"
            ),
            source="daily_check_in.soreness_score",
        )
    if recovery is not None:
        _add_evidence(
            state,
            metric="perceived_recovery_score",
            value=recovery,
            interpretation=(
                "unfavorable" if recovery <= THRESHOLDS["poor_recovery_score"] else "usable"
            ),
            source="daily_check_in.perceived_recovery_score",
        )
    if soreness is not None and soreness >= THRESHOLDS["high_soreness_score"]:
        _fire(
            state,
            "high_soreness",
            risk_flag="high_soreness",
            unfavorable_signal="high_soreness",
            confidence_reduction=True,
        )
    if recovery is not None and recovery <= THRESHOLDS["poor_recovery_score"]:
        _fire(
            state,
            "poor_subjective_recovery",
            risk_flag="poor_subjective_recovery",
            unfavorable_signal="poor_subjective_recovery",
            confidence_reduction=True,
        )
    if energy is not None and energy >= THRESHOLDS["high_motivation_score"]:
        state.favorable_signals.append("high_energy_or_motivation")


def _apply_constraint_rules(state: _SignalState, constraints: Mapping[str, Any]) -> None:
    motivation = _numeric(constraints.get("motivation_score"))
    intended_intensity = str(constraints.get("intended_intensity", "")).lower()
    if motivation is not None and motivation >= THRESHOLDS["high_motivation_score"]:
        state.favorable_signals.append("high_motivation")
    if intended_intensity in {"hard", "high", "intervals", "vo2"}:
        state.favorable_signals.append("planned_high_intensity")


def _apply_request_safety_rules(state: _SignalState, constraints: Mapping[str, Any]) -> str:
    request = constraints.get("user_request")
    if not isinstance(request, str):
        return REQUEST_ROUTE_TRAINING
    if not _is_medical_diagnosis_request(request):
        return REQUEST_ROUTE_TRAINING
    _fire(
        state,
        "request_medical_diagnosis_boundary",
        evidence={"request_category": "diagnosis"},
        risk_flag="hard_safety_medical_boundary",
        hard_safety_flag="medical_boundary",
        uncertainty=("Medical diagnosis requests are outside Baseline's training readiness scope."),
        confidence_reduction=True,
    )
    return REQUEST_ROUTE_SAFETY_REDIRECT


def _apply_memory_rules(state: _SignalState, recent_memory: Sequence[Any]) -> None:
    for item in recent_memory:
        item_text = _memory_text(item).lower()
        if "illness" in item_text or "injury" in item_text:
            _fire(
                state,
                "memory_recent_disruption",
                evidence={"memory_observation": _memory_text(item)},
                uncertainty=(
                    "Recent memory mentions disruption, so recovery signals may be less typical."
                ),
                confidence_reduction=True,
            )
            return


def _apply_external_knowledge_rule(state: _SignalState, include_external_knowledge: bool) -> None:
    if include_external_knowledge:
        _fire(
            state,
            "external_knowledge_requested",
            uncertainty="External evidence must stay separate from personal readiness evidence.",
            confidence_reduction=True,
        )


def _apply_conflict_rules(state: _SignalState, reasoning_input: ReasoningInput) -> None:
    poor_recovery = (
        bool(
            {"elevated_rhr", "high_sleep_debt", "poor_subjective_recovery", "high_soreness"}
            & set(state.risk_flags)
        )
        or "unfavorable_hrv" in state.unfavorable_signals
    )
    motivated = bool(
        {"high_motivation", "planned_high_intensity", "high_energy_or_motivation"}
        & set(state.favorable_signals)
    )
    if motivated and poor_recovery:
        _fire(
            state,
            "conflict_high_motivation_poor_recovery",
            evidence={
                "favorable": state.favorable_signals,
                "unfavorable": state.unfavorable_signals,
            },
            risk_flag="conflicting_signals",
            uncertainty="High motivation conflicts with poorer recovery indicators.",
            confidence_reduction=True,
        )

    recovery = None
    if reasoning_input.daily_check_in is not None:
        recovery = _numeric(reasoning_input.daily_check_in.get("perceived_recovery_score"))
    if recovery is not None and recovery >= 8 and "elevated_rhr" in state.risk_flags:
        _fire(
            state,
            "conflict_good_subjective_recovery_elevated_rhr",
            risk_flag="conflicting_signals",
            uncertainty="Subjective recovery is good, but resting HR is elevated.",
            confidence_reduction=True,
        )
    if "favorable_hrv" in state.favorable_signals and "high_sleep_debt" in state.risk_flags:
        _fire(
            state,
            "conflict_favorable_hrv_sleep_debt",
            risk_flag="conflicting_signals",
            uncertainty="HRV is favorable, but sleep debt is high.",
            confidence_reduction=True,
        )


def _readiness_state(state: _SignalState, completeness: float | None) -> tuple[ReadinessState, str]:
    if "data_quality_low_readiness" in state.risk_flags:
        return ReadinessState.insufficient_data, "data_quality"
    if state.hard_safety_flags:
        state.risk_flags.append("physiology_low_readiness")
        return ReadinessState.low, "physiology"

    unfavorable_weight = 0
    for signal in state.unfavorable_signals:
        major_signals = {
            "elevated_rhr",
            "high_sleep_debt",
            "high_soreness",
            "poor_subjective_recovery",
        }
        unfavorable_weight += 2 if signal in major_signals else 1

    if "conflicting_signals" in state.risk_flags:
        return ReadinessState.mixed, "conflict"
    if unfavorable_weight >= 4:
        state.risk_flags.append("physiology_low_readiness")
        return ReadinessState.low, "physiology"
    if unfavorable_weight >= 2:
        return ReadinessState.moderate, "physiology"
    if completeness is not None and completeness < 0.75:
        return ReadinessState.moderate, "data_quality"
    return ReadinessState.high, "physiology"


def _recommendation_band(
    readiness_state: ReadinessState,
    state: _SignalState,
) -> RecommendationBand:
    if readiness_state == ReadinessState.insufficient_data:
        band = RecommendationBand.insufficient_data
    elif readiness_state == ReadinessState.high:
        band = RecommendationBand.hard_training_ok
    elif readiness_state == ReadinessState.moderate:
        band = RecommendationBand.moderate
    elif readiness_state == ReadinessState.mixed:
        band = RecommendationBand.moderate_or_upper_body
    else:
        band = RecommendationBand.easy_or_recovery

    for risk_flag in _unique(state.risk_flags):
        ceiling = RISK_FLAG_BAND_CEILINGS.get(risk_flag)
        if ceiling is not None and BAND_RANK[band] > BAND_RANK[ceiling]:
            band = ceiling
            _fire(
                state,
                f"conservative_band_ceiling_{risk_flag}",
                evidence={"risk_flag": risk_flag, "ceiling": ceiling.value},
            )
    return band


def _confidence_level(state: _SignalState, completeness: float | None) -> ConfidenceLevel:
    level = 2
    if completeness is not None and completeness < 0.8:
        level -= 1
    level -= min(2, state.confidence_reductions)
    if state.hard_safety_flags or "data_quality_low_readiness" in state.risk_flags:
        level = 0
    level = max(0, min(2, level))
    return [ConfidenceLevel.low, ConfidenceLevel.medium, ConfidenceLevel.high][level]


def _goal_tradeoffs(
    active_goals: Sequence[Any],
    state: _SignalState,
    recommendation_band: RecommendationBand,
) -> list[JsonDict]:
    tradeoffs: list[JsonDict] = []
    for goal in active_goals:
        category = _goal_value(goal, "category")
        if category is None:
            continue
        priority = _goal_value(goal, "priority")
        easy_or_lower = BAND_RANK[recommendation_band] <= BAND_RANK[RecommendationBand.easy]
        if category in {"vo2_max", "strength"} and easy_or_lower:
            tradeoff = "Adaptation work is de-emphasized today to protect recovery and reduce risk."
        elif category in {"recovery", "sleep"} and state.risk_flags:
            tradeoff = "Recovery protection is prioritized because risk flags are active."
        elif category == "cognitive_performance" and "high_sleep_debt" in state.risk_flags:
            tradeoff = "Lower intensity protects cognitive performance after sleep debt."
        else:
            tradeoff = "Current recommendation remains compatible with this goal."
        tradeoffs.append(
            {
                "goal": category,
                "priority": priority,
                "tradeoff": tradeoff,
            }
        )
    return tradeoffs


def _candidate_options(
    band: RecommendationBand,
    state: _SignalState,
    confidence: ConfidenceLevel,
) -> list[JsonDict]:
    primary = {
        "label": "Primary conservative option",
        "recommendation_band": band.value,
        "rationale": "Matches deterministic readiness rules and active risk flags.",
    }
    options = [primary]
    if confidence != ConfidenceLevel.high or state.risk_flags:
        safer = _lower_band(band)
        options.append(
            {
                "label": "Lower-risk alternative",
                "recommendation_band": safer.value,
                "rationale": (
                    "Use this if subjective recovery is worse than the available data suggests."
                ),
            }
        )
    if "conflicting_signals" in state.risk_flags:
        options.append(
            {
                "label": "Resolve uncertainty first",
                "recommendation_band": RecommendationBand.easy.value,
                "rationale": (
                    "Conflicting signals make easy work safer until subjective context is clearer."
                ),
            }
        )
    return options


def _lower_band(band: RecommendationBand) -> RecommendationBand:
    if band in {RecommendationBand.insufficient_data, RecommendationBand.rest}:
        return band
    lower_rank = max(1, BAND_RANK[band] - 1)
    for candidate, rank in BAND_RANK.items():
        if rank == lower_rank:
            return candidate
    return RecommendationBand.recovery


def _feature_value(features: Mapping[str, Any], section: str, key: str) -> Any | None:
    section_data = features.get(section)
    if not isinstance(section_data, Mapping):
        return None
    values = section_data.get("values")
    if not isinstance(values, Mapping):
        return None
    raw_value = values.get(key)
    if not isinstance(raw_value, Mapping) or raw_value.get("status") != "computed":
        return None
    return raw_value.get("value")


def _data_quality(features: Mapping[str, Any]) -> Mapping[str, Any]:
    data_quality = features.get("data_quality")
    return data_quality if isinstance(data_quality, Mapping) else {}


def _quality_flags(features: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    data_quality = _data_quality(features)
    raw_flags = data_quality.get("flags")
    if isinstance(raw_flags, list):
        flags.extend(str(flag) for flag in raw_flags)
    raw_anomalies = features.get("anomaly_flags")
    if isinstance(raw_anomalies, list):
        flags.extend(str(flag) for flag in raw_anomalies)
    for section_name in (
        "sleep_features",
        "hrv_features",
        "rhr_features",
        "training_load_features",
        "recovery_features",
        "goal_features",
    ):
        section = features.get(section_name)
        if not isinstance(section, Mapping):
            continue
        section_quality = section.get("data_quality")
        if isinstance(section_quality, Mapping) and isinstance(section_quality.get("flags"), list):
            flags.extend(str(flag) for flag in section_quality["flags"])
    return _unique(flags)


def _max_density_count(modality_density: Any | None) -> int:
    if not isinstance(modality_density, Mapping):
        return 0
    max_count = 0
    for value in modality_density.values():
        if isinstance(value, Mapping):
            count = _numeric(value.get("value"))
            if count is not None:
                max_count = max(max_count, int(count))
    return max_count


def _is_elevated_rhr(rhr_pct: Any | None, rhr_bpm: Any | None) -> bool:
    pct = _numeric(rhr_pct)
    bpm = _numeric(rhr_bpm)
    return (pct is not None and pct >= THRESHOLDS["elevated_rhr_pct"]) or (
        bpm is not None and bpm >= THRESHOLDS["elevated_rhr_bpm"]
    )


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _truthy(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else str(value).lower() == "true"


def _is_medical_diagnosis_request(request: str) -> bool:
    normalized = request.lower()
    diagnosis_terms = ("diagnose", "diagnosis", "condition", "disease", "why my")
    return any(term in normalized for term in diagnosis_terms)


def _memory_text(item: Any) -> str:
    if isinstance(item, Mapping):
        for key in ("observation", "summary", "text"):
            value = item.get(key)
            if isinstance(value, str):
                return value
    return str(item)


def _goal_value(goal: Any, key: str) -> Any | None:
    raw = goal.get(key) if isinstance(goal, Mapping) else getattr(goal, key, None)
    if raw is not None and hasattr(raw, "value"):
        return raw.value
    return raw


def _add_evidence(
    state: _SignalState,
    *,
    metric: str,
    value: Any,
    interpretation: str,
    source: str,
) -> None:
    state.evidence_items.append(
        {
            "metric": metric,
            "value": value,
            "interpretation": interpretation,
            "source": source,
        }
    )


def _follow_up(state: _SignalState, question: str, reason: str) -> None:
    item = {"question": question, "reason": reason}
    if item not in state.follow_up_questions:
        state.follow_up_questions.append(item)


def _fire(
    state: _SignalState,
    rule_id: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    risk_flag: str | None = None,
    hard_safety_flag: str | None = None,
    unfavorable_signal: str | None = None,
    uncertainty: str | None = None,
    confidence_reduction: bool = False,
) -> None:
    state.rules_fired.append(
        {
            "rule_id": rule_id,
            "evidence": dict(evidence or {}),
            "risk_flag": risk_flag,
            "hard_safety_flag": hard_safety_flag,
            "confidence_reduction": confidence_reduction,
        }
    )
    if risk_flag is not None:
        state.risk_flags.append(risk_flag)
    if hard_safety_flag is not None:
        state.hard_safety_flags.append(hard_safety_flag)
    if unfavorable_signal is not None:
        state.unfavorable_signals.append(unfavorable_signal)
    if uncertainty is not None and uncertainty not in state.uncertainty:
        state.uncertainty.append(uncertainty)
    if confidence_reduction:
        state.confidence_reductions += 1


def _canonical_input_payload(reasoning_input: ReasoningInput) -> JsonDict:
    return {
        "target_date": reasoning_input.target_date.isoformat(),
        "features": reasoning_input.features,
        "active_goals": [_jsonable(item) for item in reasoning_input.active_goals],
        "recent_memory": [_jsonable(item) for item in reasoning_input.recent_memory],
        "user_constraints": reasoning_input.user_constraints,
        "daily_check_in": reasoning_input.daily_check_in,
        "include_external_knowledge": reasoning_input.include_external_knowledge,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(raw)
            for key, raw in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dt.date | dt.datetime | UUID):
        return str(value)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _unique(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
