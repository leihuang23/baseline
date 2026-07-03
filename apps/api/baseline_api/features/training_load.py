"""Training load, workout density, and VO2 max trend calculations."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from baseline_api.features.feature_types import (
    FeatureBundle,
    JsonDict,
    calculation_metadata,
    completeness,
    computed_value,
    feature_status,
    gap_value,
    unique_ordered,
)


@dataclass(frozen=True, slots=True)
class WorkoutSessionInput:
    """Canonical workout session input for pure training-load calculations."""

    session_id: str
    start_time: dt.datetime
    end_time: dt.datetime | None
    modality: str
    duration_seconds: float
    distance_meters: float | None = None
    active_energy_kcal: float | None = None
    average_hr_bpm: float | None = None
    max_hr_bpm: float | None = None
    intensity_zone_distribution: dict[str, float] | None = None
    perceived_exertion: int | None = None
    muscle_group_tags: list[str] | None = None
    confidence: float = 1.0
    source_sample_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VO2SampleInput:
    """Canonical VO2 max sample input for trend calculations."""

    sample_id: str
    start_time: dt.datetime
    value: float
    source_sample_ids: tuple[str, ...] = ()


def compute_training_load_features(
    target_date: dt.date,
    workouts: list[WorkoutSessionInput],
    *,
    acute_window_days: int = 7,
    chronic_window_days: int = 28,
    density_window_days: int = 6,
    min_load_days: int = 2,
    min_hr_bpm: float = 50,
    max_hr_bpm: float = 210,
) -> JsonDict:
    """Compute acute/chronic load, ratio, and workout density for one day."""

    valid_workouts, flags = _validate_workouts(
        workouts,
        min_hr_bpm=min_hr_bpm,
        max_hr_bpm=max_hr_bpm,
    )
    daily_load = _daily_load_units(valid_workouts)
    values: JsonDict = {}

    today_load = daily_load.get(target_date, 0.0)
    values["today_load_units"] = computed_value(today_load, "load_units")

    acute_load = _ewma_load(
        daily_load,
        target_date,
        window_days=acute_window_days,
        min_load_days=min_load_days,
    )
    chronic_load = _ewma_load(
        daily_load,
        target_date,
        window_days=chronic_window_days,
        min_load_days=min_load_days,
    )

    if acute_load is not None:
        values["acute_load_units"] = computed_value(acute_load, "load_units")
    else:
        values["acute_load_units"] = gap_value(
            "insufficient_data",
            "not_enough_workout_history",
            "load_units",
        )
        flags.append("baseline_not_established_acute_load")

    if chronic_load is not None:
        values["chronic_load_units"] = computed_value(chronic_load, "load_units")
    else:
        values["chronic_load_units"] = gap_value(
            "insufficient_data",
            "not_enough_workout_history",
            "load_units",
        )
        flags.append("baseline_not_established_chronic_load")

    if acute_load is not None and chronic_load is not None and chronic_load > 0:
        ratio = acute_load / chronic_load
        values["acute_chronic_ratio"] = computed_value(ratio, "ratio")
        values["load_balance"] = _load_balance_value(ratio)
    else:
        values["acute_chronic_ratio"] = gap_value(
            "insufficient_data",
            "missing_load_history",
            "ratio",
        )
        values["load_balance"] = gap_value(
            "insufficient_data",
            "missing_load_history",
            "category",
        )

    density = _workout_density(valid_workouts, target_date, window_days=density_window_days)
    values["density_by_muscle_group"] = {
        "status": "computed",
        "value": density["muscle_group"],
        "unit": "structured",
    }
    values["density_by_modality"] = {
        "status": "computed",
        "value": density["modality"],
        "unit": "structured",
    }

    source_sample_ids = unique_ordered(
        source_id for workout in valid_workouts for source_id in workout.source_sample_ids
    )

    data_quality = {
        "completeness": completeness(values),
        "flags": unique_ordered(flags),
        "input_counts": {
            "target_workouts": len(
                [w for w in valid_workouts if w.start_time.date() == target_date]
            ),
            "window_workouts": len(valid_workouts),
            "acute_window_days": acute_window_days,
            "chronic_window_days": chronic_window_days,
            "density_window_days": density_window_days,
            "min_load_days": min_load_days,
            "acute_window_load_days": _load_days_in_window(
                daily_load, target_date, window_days=acute_window_days
            ),
            "chronic_window_load_days": _load_days_in_window(
                daily_load, target_date, window_days=chronic_window_days
            ),
            "acute_window_zero_load_days": acute_window_days
            - _load_days_in_window(daily_load, target_date, window_days=acute_window_days),
            "chronic_window_zero_load_days": chronic_window_days
            - _load_days_in_window(daily_load, target_date, window_days=chronic_window_days),
        },
    }

    return FeatureBundle(
        status=feature_status(values),
        values=values,
        calculation_metadata=calculation_metadata(
            formula_name="training_load_ewma_density",
            target_date=target_date,
            parameters={
                "acute_window_days": acute_window_days,
                "chronic_window_days": chronic_window_days,
                "density_window_days": density_window_days,
                "min_load_days": min_load_days,
                "load_unit_formula": (
                    "max(active_energy, duration, hr, distance signals) "
                    "* modality_factor * intensity_zone_factor * rpe_factor"
                ),
                "aggregate": "ewma",
            },
        ),
        data_quality=data_quality,
        source_sample_ids=source_sample_ids,
    ).to_dict()


def compute_vo2_features(
    target_date: dt.date,
    vo2_samples: list[VO2SampleInput],
    *,
    trend_window_days: int = 28,
    min_samples: int = 2,
) -> JsonDict:
    """Compute VO2 max trend when Apple Health samples are available."""

    valid_samples = [
        sample for sample in vo2_samples if math.isfinite(sample.value) and 20 <= sample.value <= 90
    ]

    flags: list[str] = []
    for sample in vo2_samples:
        if not math.isfinite(sample.value) or not 20 <= sample.value <= 90:
            flags.append("anomalous_vo2_max")

    window_start = target_date - dt.timedelta(days=trend_window_days - 1)
    window_samples = [
        sample
        for sample in valid_samples
        if window_start <= sample.start_time.date() <= target_date
    ]

    values: JsonDict = {}
    if len(window_samples) >= min_samples:
        slope_per_day, intercept = _linear_regression(
            [
                ((sample.start_time.date() - target_date).days, sample.value)
                for sample in window_samples
            ]
        )
        slope_per_week = slope_per_day * 7
        recent_value = max(
            window_samples,
            key=lambda sample: sample.start_time,
        ).value
        values["trend_slope_per_week"] = computed_value(slope_per_week, "mL/kg/min/week")
        values["recent_value"] = computed_value(recent_value, "mL/kg/min")
        values["trend_direction"] = {
            "status": "computed",
            "value": _trend_direction(slope_per_week),
            "unit": "category",
        }
    else:
        values["trend_slope_per_week"] = gap_value(
            "insufficient_data",
            "not_enough_vo2_samples",
            "mL/kg/min/week",
        )
        values["recent_value"] = gap_value(
            "insufficient_data",
            "not_enough_vo2_samples",
            "mL/kg/min",
        )
        values["trend_direction"] = gap_value(
            "insufficient_data",
            "not_enough_vo2_samples",
            "category",
        )
        flags.append("missing_vo2_max")

    source_sample_ids = unique_ordered(
        source_id for sample in window_samples for source_id in sample.source_sample_ids
    )

    data_quality = {
        "completeness": completeness(values),
        "flags": unique_ordered(flags),
        "input_counts": {
            "window_samples": len(window_samples),
            "trend_window_days": trend_window_days,
            "min_samples": min_samples,
        },
    }

    return FeatureBundle(
        status=feature_status(values),
        values=values,
        calculation_metadata=calculation_metadata(
            formula_name="vo2_max_linear_trend",
            target_date=target_date,
            parameters={
                "trend_window_days": trend_window_days,
                "min_samples": min_samples,
                "regression": "ordinary_least_squares",
            },
        ),
        data_quality=data_quality,
        source_sample_ids=source_sample_ids,
    ).to_dict()


def _validate_workouts(
    workouts: list[WorkoutSessionInput],
    *,
    min_hr_bpm: float,
    max_hr_bpm: float,
) -> tuple[list[WorkoutSessionInput], list[str]]:
    flags: list[str] = []
    valid: list[WorkoutSessionInput] = []
    for workout in workouts:
        if workout.confidence < 1.0:
            flags.append("low_confidence_workout")
        if not _is_finite_positive(workout.duration_seconds):
            flags.append("anomalous_workout_duration")
            continue
        if workout.duration_seconds > 8 * 3600:
            flags.append("anomalous_workout_duration")
            continue
        if (
            workout.average_hr_bpm is not None
            and not min_hr_bpm <= workout.average_hr_bpm <= max_hr_bpm
        ):
            flags.append("anomalous_workout_hr")
            continue
        valid.append(workout)
    return valid, flags


def _session_load_units(workout: WorkoutSessionInput) -> float:
    duration_minutes = workout.duration_seconds / 60.0
    load_candidates: list[float] = []

    if workout.active_energy_kcal is not None and workout.active_energy_kcal > 0:
        load_candidates.append(workout.active_energy_kcal / 50.0)

    if workout.average_hr_bpm is not None and workout.max_hr_bpm is not None:
        load_candidates.append(
            duration_minutes * _hr_fraction(workout.average_hr_bpm, workout.max_hr_bpm)
        )

    distance_load = _distance_load_units(workout)
    if distance_load is not None:
        load_candidates.append(distance_load)

    if not load_candidates:
        load_candidates.append(duration_minutes / 10.0)

    return (
        max(load_candidates)
        * _modality_factor(workout.modality)
        * _intensity_zone_factor(workout.intensity_zone_distribution)
        * _rpe_factor(workout.perceived_exertion)
    )


def _rpe_factor(perceived_exertion: int | None) -> float:
    if perceived_exertion is None:
        return 1.0
    return max(0.6, perceived_exertion / 5.0)


def _hr_fraction(average_hr_bpm: float, max_hr_bpm: float) -> float:
    resting = 60.0
    numerator = max(0.0, average_hr_bpm - resting)
    denominator = max(1.0, max_hr_bpm - resting)
    return min(1.0, numerator / denominator)


def _distance_load_units(workout: WorkoutSessionInput) -> float | None:
    if workout.distance_meters is None or workout.distance_meters <= 0:
        return None

    distance_km = workout.distance_meters / 1000.0
    modality = workout.modality.lower()
    if modality in {"run", "running"}:
        return distance_km * 1.1
    if modality in {"cycle", "cycling", "bike"}:
        return distance_km * 0.35
    if modality in {"swim", "swimming"}:
        return distance_km * 2.0
    if modality in {"walk", "walking", "hike", "hiking"}:
        return distance_km * 0.5
    return distance_km * 0.2


def _modality_factor(modality: str) -> float:
    normalized = modality.lower()
    if normalized in {"hiit", "kettlebell"}:
        return 1.2
    if normalized in {"run", "running", "strength", "strength_training"}:
        return 1.1
    if normalized in {"cycle", "cycling", "swim", "swimming", "team_sport"}:
        return 1.0
    if normalized in {"walk", "walking", "yoga", "mobility"}:
        return 0.75
    return 1.0


def _intensity_zone_factor(distribution: dict[str, float] | None) -> float:
    if not distribution:
        return 1.0

    zone_factors = {
        "1": 0.65,
        "2": 0.8,
        "3": 1.0,
        "4": 1.25,
        "5": 1.5,
    }
    weighted_total = 0.0
    total = 0.0
    for raw_zone, raw_value in distribution.items():
        if not isinstance(raw_value, int | float) or raw_value <= 0:
            continue
        zone = raw_zone.lower().removeprefix("zone").removeprefix("_").removeprefix("-")
        factor = zone_factors.get(zone)
        if factor is None:
            continue
        weighted_total += raw_value * factor
        total += raw_value

    if total <= 0:
        return 1.0
    return weighted_total / total


def _daily_load_units(workouts: list[WorkoutSessionInput]) -> dict[dt.date, float]:
    load_by_day: dict[dt.date, list[float]] = {}
    for workout in workouts:
        load_by_day.setdefault(workout.start_time.date(), []).append(_session_load_units(workout))
    return {day: math.fsum(loads) for day, loads in sorted(load_by_day.items())}


def _ewma_load(
    daily_load: dict[dt.date, float],
    target_date: dt.date,
    *,
    window_days: int,
    min_load_days: int,
) -> float | None:
    if _load_days_in_window(daily_load, target_date, window_days=window_days) < min_load_days:
        return None

    window_start = target_date - dt.timedelta(days=window_days - 1)
    alpha = 2.0 / (window_days + 1.0)
    ewma = 0.0
    for offset in range(window_days):
        day = window_start + dt.timedelta(days=offset)
        load = daily_load.get(day, 0.0)
        ewma = alpha * load + (1.0 - alpha) * ewma
    return float(ewma)


def _load_days_in_window(
    daily_load: dict[dt.date, float],
    target_date: dt.date,
    *,
    window_days: int,
) -> int:
    window_start = target_date - dt.timedelta(days=window_days - 1)
    return sum(
        1 for day, load in daily_load.items() if window_start <= day <= target_date and load > 0
    )


def _load_balance_value(ratio: float) -> JsonDict:
    if ratio < 0.8:
        category = "low_acute_load"
    elif ratio < 1.3:
        category = "balanced"
    elif ratio < 1.5:
        category = "elevated"
    else:
        category = "high_spike"
    return {
        "status": "computed",
        "value": category,
        "unit": "category",
    }


def _workout_density(
    workouts: list[WorkoutSessionInput],
    target_date: dt.date,
    *,
    window_days: int,
) -> dict[str, dict[str, JsonDict]]:
    window_start = target_date - dt.timedelta(days=window_days - 1)
    window_workouts = [
        workout for workout in workouts if window_start <= workout.start_time.date() <= target_date
    ]

    muscle_counts: dict[str, int] = {}
    modality_counts: dict[str, int] = {}
    for workout in window_workouts:
        modality = workout.modality or "unknown"
        modality_counts[modality] = modality_counts.get(modality, 0) + 1
        for tag in workout.muscle_group_tags or []:
            muscle_counts[tag] = muscle_counts.get(tag, 0) + 1

    return {
        "muscle_group": {
            tag: {
                "status": "computed",
                "value": count,
                "unit": "sessions",
                "window_days": window_days,
            }
            for tag, count in sorted(muscle_counts.items())
        },
        "modality": {
            modality: {
                "status": "computed",
                "value": count,
                "unit": "sessions",
                "window_days": window_days,
            }
            for modality, count in sorted(modality_counts.items())
        },
    }


def _linear_regression(points: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(points)
    if n == 0:
        return 0.0, 0.0
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return 0.0, mean_y
    slope = numerator / denominator
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _trend_direction(slope_per_week: float) -> str:
    if slope_per_week > 0.15:
        return "improving"
    if slope_per_week < -0.15:
        return "declining"
    return "stable"


def _is_finite_positive(value: float) -> bool:
    return math.isfinite(value) and value > 0
