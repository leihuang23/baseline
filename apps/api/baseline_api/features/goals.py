"""Goal-relevant deterministic indicators for feature assembly."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

from baseline_api.features.feature_types import (
    FeatureBundle,
    JsonDict,
    calculation_metadata,
    completeness,
    unique_ordered,
)
from baseline_api.features.training_load import (
    VO2SampleInput,
    WorkoutSessionInput,
    compute_vo2_features,
)

DEFAULT_GOAL_CATEGORIES = [
    "vo2_max",
    "strength",
    "recovery",
    "sleep",
    "cognitive_performance",
    "long_term_wellness",
]
STRENGTH_MODALITY_TERMS = (
    "strength",
    "resistance",
    "weights",
    "weight_training",
    "traditional_strength_training",
    "functional_strength_training",
    "kettlebell",
)


def compute_goal_features(
    target_date: dt.date,
    vo2_samples: list[VO2SampleInput],
    *,
    active_goal_categories: list[str] | None = None,
    sleep_features: Mapping[str, Any] | None = None,
    hrv_features: Mapping[str, Any] | None = None,
    rhr_features: Mapping[str, Any] | None = None,
    training_load_features: Mapping[str, Any] | None = None,
    recovery_features: Mapping[str, Any] | None = None,
    workouts: list[WorkoutSessionInput] | None = None,
    daily_check_in: Mapping[str, Any] | None = None,
    vo2_features: Mapping[str, Any] | None = None,
) -> JsonDict:
    """Compute deterministic goal indicators without implying unavailable measurements."""

    if vo2_features is None:
        vo2_features = compute_vo2_features(target_date, vo2_samples)
    categories = active_goal_categories or DEFAULT_GOAL_CATEGORIES
    indicators = {
        "vo2_max": _vo2_indicator(vo2_features),
        "strength": _strength_indicator(target_date, workouts or []),
        "cognitive_performance": _cognitive_indicator(
            sleep_features or {},
            hrv_features or {},
            rhr_features or {},
            recovery_features or {},
            daily_check_in or {},
        ),
        "long_term_wellness": _wellness_indicator(
            sleep_features or {},
            training_load_features or {},
            recovery_features or {},
            vo2_features,
        ),
        "recovery": _recovery_indicator(recovery_features or {}),
        "sleep": _sleep_indicator(sleep_features or {}),
    }
    selected_indicators = {
        category: indicators.get(
            category,
            _unavailable_indicator(
                f"No deterministic indicator is implemented for goal category '{category}'.",
                ["goal_category_not_supported"],
            ),
        )
        for category in unique_ordered(categories)
    }

    values: JsonDict = {
        "vo2_trend": vo2_features,
        "goal_indicators": {
            "status": "computed",
            "value": selected_indicators,
            "unit": "structured",
        },
    }

    source_sample_ids = unique_ordered(
        [
            *list(vo2_features.get("source_sample_ids", [])),
            *[
                source_id
                for workout in workouts or []
                for source_id in workout.source_sample_ids
                if _is_strength_workout(workout)
            ],
        ]
    )
    missing_data = unique_ordered(
        item
        for indicator in selected_indicators.values()
        for item in indicator.get("missing_data", [])
    )

    data_quality = {
        "completeness": completeness(values),
        "indicator_completeness": _indicator_completeness(selected_indicators),
        "flags": unique_ordered(
            [
                *list(vo2_features.get("data_quality", {}).get("flags", [])),
                *(["missing_goal_indicator_inputs"] if missing_data else []),
            ]
        ),
        "input_counts": {
            "active_goal_categories": len(categories),
            "vo2_samples": vo2_features.get("data_quality", {})
            .get("input_counts", {})
            .get("window_samples", 0),
            "strength_workouts": sum(
                1 for workout in workouts or [] if _is_strength_workout(workout)
            ),
        },
        "missing_data": missing_data,
    }

    return FeatureBundle(
        status=vo2_features["status"],
        values=values,
        calculation_metadata=calculation_metadata(
            formula_name="goal_indicators",
            target_date=target_date,
            parameters={
                "active_goal_categories": categories,
                "strength_recent_window_days": 14,
                "strength_baseline_window_days": 14,
                "indicator_categories": list(selected_indicators),
            },
        ),
        data_quality=data_quality,
        source_sample_ids=source_sample_ids,
    ).to_dict()


def _vo2_indicator(vo2_features: Mapping[str, Any]) -> JsonDict:
    direction = _entry_value(vo2_features, "trend_direction")
    recent_value = _entry_value(vo2_features, "recent_value")
    if direction is None:
        return _unavailable_indicator(
            "VO2 max trend is unavailable because there are not enough recent VO2 samples.",
            ["vo2_max_samples"],
        )
    summary = f"VO2 max trend is {direction}."
    if recent_value is not None:
        summary = f"VO2 max trend is {direction}; latest estimate is {recent_value} mL/kg/min."
    return _indicator(
        summary=summary,
        evidence_refs=[
            "goal_features.values.vo2_trend.values.trend_direction",
            "goal_features.values.vo2_trend.values.recent_value",
        ],
        confidence="medium",
        direction=str(direction),
        value=recent_value,
    )


def _strength_indicator(target_date: dt.date, workouts: list[WorkoutSessionInput]) -> JsonDict:
    strength_workouts = [workout for workout in workouts if _is_strength_workout(workout)]
    if not strength_workouts:
        return _unavailable_indicator(
            (
                "Strength consistency proxy is unavailable because no strength or "
                "kettlebell workouts were found."
            ),
            ["strength_or_kettlebell_workouts"],
        )

    recent_start = target_date - dt.timedelta(days=13)
    baseline_start = target_date - dt.timedelta(days=27)
    baseline_end = target_date - dt.timedelta(days=14)
    recent = [
        workout
        for workout in strength_workouts
        if recent_start <= workout.start_time.date() <= target_date
    ]
    baseline = [
        workout
        for workout in strength_workouts
        if baseline_start <= workout.start_time.date() <= baseline_end
    ]
    recent_minutes = _duration_minutes(recent)
    baseline_minutes = _duration_minutes(baseline)
    recent_energy = _active_energy(recent)
    baseline_energy = _active_energy(baseline)
    delta_minutes = recent_minutes - baseline_minutes
    trend = "stable"
    if delta_minutes >= 30 or len(recent) > len(baseline):
        trend = "building"
    elif delta_minutes <= -30 or len(recent) < len(baseline):
        trend = "declining"

    missing_data = [] if baseline else ["strength_baseline_window"]
    return _indicator(
        summary=(
            "Strength proxy is based on consistency, duration, and active energy; "
            "it does not measure lifted load."
        ),
        evidence_refs=["goal_features.values.goal_indicators.strength"],
        confidence="medium" if baseline else "low",
        missing_data=missing_data,
        trend=trend,
        recent_sessions=len(recent),
        baseline_sessions=len(baseline),
        recent_minutes=round(recent_minutes, 1),
        baseline_minutes=round(baseline_minutes, 1),
        recent_active_energy_kcal=round(recent_energy, 1) if recent_energy is not None else None,
        baseline_active_energy_kcal=round(baseline_energy, 1)
        if baseline_energy is not None
        else None,
    )


def _cognitive_indicator(
    sleep_features: Mapping[str, Any],
    hrv_features: Mapping[str, Any],
    rhr_features: Mapping[str, Any],
    recovery_features: Mapping[str, Any],
    daily_check_in: Mapping[str, Any],
) -> JsonDict:
    refs: list[str] = []
    missing: list[str] = []
    concerns: list[str] = []

    sleep_debt = _entry_value(sleep_features, "sleep_debt_hours")
    if sleep_debt is None:
        missing.append("sleep_debt")
    else:
        refs.append("sleep_features.values.sleep_debt_hours")
        if float(sleep_debt) >= 1.5:
            concerns.append("sleep_debt")

    hrv_pct = _entry_value(hrv_features, "deviation_pct")
    if hrv_pct is None:
        missing.append("hrv_deviation")
    else:
        refs.append("hrv_features.values.deviation_pct")
        if float(hrv_pct) <= -10:
            concerns.append("low_hrv")

    rhr_pct = _entry_value(rhr_features, "deviation_pct")
    if rhr_pct is None:
        missing.append("rhr_deviation")
    else:
        refs.append("rhr_features.values.deviation_pct")
        if float(rhr_pct) >= 7:
            concerns.append("elevated_rhr")

    recovery_level = _entry_value(recovery_features, "level")
    if recovery_level is not None:
        refs.append("recovery_features.values.level")

    energy = _numeric(daily_check_in.get("energy_score"))
    stress = _numeric(daily_check_in.get("stress_score"))
    if energy is not None:
        refs.append("daily_check_in.energy_score")
        if energy <= 4:
            concerns.append("low_energy")
    if stress is not None:
        refs.append("daily_check_in.stress_score")
        if stress >= 7:
            concerns.append("high_stress")

    if len(refs) < 2:
        return _unavailable_indicator(
            (
                "Cognitive-readiness proxy needs at least two sleep, recovery, "
                "HRV/RHR, or check-in inputs."
            ),
            missing or ["sleep_or_recovery_or_checkin_inputs"],
        )
    state = "strained" if concerns else "supported"
    return _indicator(
        summary=(
            f"Cognitive-readiness proxy is {state} from available recovery and check-in signals."
        ),
        evidence_refs=unique_ordered(refs),
        confidence="medium" if not missing else "low",
        missing_data=missing,
        state=state,
        concerns=unique_ordered(concerns),
    )


def _wellness_indicator(
    sleep_features: Mapping[str, Any],
    training_load_features: Mapping[str, Any],
    recovery_features: Mapping[str, Any],
    vo2_features: Mapping[str, Any],
) -> JsonDict:
    refs: list[str] = []
    missing: list[str] = []
    signals: list[str] = []

    for key, ref in [
        ("sleep_debt_hours", "sleep_features.values.sleep_debt_hours"),
    ]:
        if _entry_value(sleep_features, key) is None:
            missing.append(key)
        else:
            refs.append(ref)
    for key, ref in [
        ("load_balance", "training_load_features.values.load_balance"),
        ("level", "recovery_features.values.level"),
    ]:
        source = training_load_features if key == "load_balance" else recovery_features
        value = _entry_value(source, key)
        if value is None:
            missing.append(key)
        else:
            refs.append(ref)
            signals.append(f"{key}:{value}")
    direction = _entry_value(vo2_features, "trend_direction")
    if direction is None:
        missing.append("vo2_trend")
    else:
        refs.append("goal_features.values.vo2_trend.values.trend_direction")
        signals.append(f"vo2:{direction}")

    if len(refs) < 3:
        return _unavailable_indicator(
            "Long-term wellness proxy needs sleep, training balance, recovery, and VO2 context.",
            missing,
        )
    return _indicator(
        summary=(
            "Long-term wellness proxy reflects lifestyle consistency signals only; "
            "it is not diagnostic."
        ),
        evidence_refs=unique_ordered(refs),
        confidence="medium" if not missing else "low",
        missing_data=missing,
        signals=unique_ordered(signals),
    )


def _recovery_indicator(recovery_features: Mapping[str, Any]) -> JsonDict:
    level = _entry_value(recovery_features, "level")
    if level is None:
        return _unavailable_indicator(
            "Recovery indicator is unavailable because recovery confidence could not be computed.",
            ["recovery_confidence"],
        )
    return _indicator(
        summary=f"Recovery confidence is {level}.",
        evidence_refs=["recovery_features.values.level"],
        confidence="medium",
        level=level,
    )


def _sleep_indicator(sleep_features: Mapping[str, Any]) -> JsonDict:
    sleep_debt = _entry_value(sleep_features, "sleep_debt_hours")
    if sleep_debt is None:
        return _unavailable_indicator(
            "Sleep indicator is unavailable because sleep debt could not be computed.",
            ["sleep_duration_or_sleep_need"],
        )
    status = "protected" if float(sleep_debt) <= 0.5 else "debt_present"
    return _indicator(
        summary=f"Sleep indicator status is {status} with {sleep_debt} hours of sleep debt.",
        evidence_refs=["sleep_features.values.sleep_debt_hours"],
        confidence="medium",
        status_label=status,
        sleep_debt_hours=sleep_debt,
    )


def _indicator(
    *,
    summary: str,
    evidence_refs: list[str],
    confidence: str,
    missing_data: list[str] | None = None,
    **extra: Any,
) -> JsonDict:
    return {
        "status": "computed",
        "summary": summary,
        "confidence": confidence,
        "evidence_refs": unique_ordered(evidence_refs),
        "missing_data": unique_ordered(missing_data or []),
        **{key: value for key, value in extra.items() if value is not None},
    }


def _unavailable_indicator(summary: str, missing_data: list[str]) -> JsonDict:
    return {
        "status": "unavailable",
        "summary": summary,
        "confidence": "low",
        "evidence_refs": [],
        "missing_data": unique_ordered(missing_data),
    }


def _indicator_completeness(indicators: Mapping[str, Mapping[str, Any]]) -> float:
    if not indicators:
        return 0.0
    computed = sum(1 for indicator in indicators.values() if indicator.get("status") == "computed")
    return round(computed / len(indicators), 4)


def _entry_value(features: Mapping[str, Any], key: str) -> Any | None:
    values = features.get("values")
    if not isinstance(values, Mapping):
        return None
    entry = values.get(key)
    if not isinstance(entry, Mapping) or entry.get("status") != "computed":
        return None
    return entry.get("value")


def _is_strength_workout(workout: WorkoutSessionInput) -> bool:
    modality = workout.modality.lower()
    return any(term in modality for term in STRENGTH_MODALITY_TERMS)


def _duration_minutes(workouts: list[WorkoutSessionInput]) -> float:
    return sum(max(0.0, workout.duration_seconds) for workout in workouts) / 60


def _active_energy(workouts: list[WorkoutSessionInput]) -> float | None:
    values = [workout.active_energy_kcal for workout in workouts if workout.active_energy_kcal]
    if not values:
        return None
    return sum(values)


def _numeric(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None
