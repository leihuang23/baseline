"""Cardiovascular baseline feature calculations."""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass
from typing import Literal

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
class CardioSampleInput:
    """Canonical scalar metric input for pure cardiovascular calculations."""

    sample_id: str
    start_time: dt.datetime
    value: float
    source_sample_ids: tuple[str, ...] = ()
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class _MetricSpec:
    metric_type: str
    formula_name: str
    today_key: str
    baseline_key: str
    deviation_key: str
    unit: str
    valid_min: float
    valid_max: float
    conflict_spread: float
    direction: str


_HRV_SPEC = _MetricSpec(
    metric_type="heart_rate_variability",
    formula_name="hrv_rolling_baseline_deviation",
    today_key="today_ms",
    baseline_key="baseline_ms",
    deviation_key="deviation_ms",
    unit="ms",
    valid_min=10,
    valid_max=250,
    conflict_spread=20,
    direction="higher_is_generally_better",
)

_RHR_SPEC = _MetricSpec(
    metric_type="resting_heart_rate",
    formula_name="resting_hr_rolling_baseline_deviation",
    today_key="today_bpm",
    baseline_key="baseline_bpm",
    deviation_key="deviation_bpm",
    unit="bpm",
    valid_min=30,
    valid_max=120,
    conflict_spread=8,
    direction="lower_is_generally_better",
)


def compute_hrv_features(
    target_date: dt.date,
    samples: list[CardioSampleInput],
    *,
    baseline_window_days: int = 28,
    min_baseline_days: int = 7,
    stale_after_days: int = 1,
) -> JsonDict:
    """Compute HRV rolling baseline and target-day deviation."""

    return _compute_metric_features(
        target_date,
        samples,
        spec=_HRV_SPEC,
        baseline_window_days=baseline_window_days,
        min_baseline_days=min_baseline_days,
        stale_after_days=stale_after_days,
    )


def compute_rhr_features(
    target_date: dt.date,
    samples: list[CardioSampleInput],
    *,
    baseline_window_days: int = 28,
    min_baseline_days: int = 7,
    stale_after_days: int = 1,
) -> JsonDict:
    """Compute resting-HR rolling baseline and target-day deviation."""

    return _compute_metric_features(
        target_date,
        samples,
        spec=_RHR_SPEC,
        baseline_window_days=baseline_window_days,
        min_baseline_days=min_baseline_days,
        stale_after_days=stale_after_days,
    )


def _compute_metric_features(
    target_date: dt.date,
    samples: list[CardioSampleInput],
    *,
    spec: _MetricSpec,
    baseline_window_days: int,
    min_baseline_days: int,
    stale_after_days: int,
) -> JsonDict:
    flags: list[str] = []
    valid_samples: list[CardioSampleInput] = []
    for sample in samples:
        if sample.confidence < 1.0:
            flags.append(f"low_confidence_{spec.metric_type}")
        if not math.isfinite(sample.value) or not spec.valid_min <= sample.value <= spec.valid_max:
            flags.append(f"anomalous_{spec.metric_type}")
            continue
        valid_samples.append(sample)

    target_samples = [
        sample for sample in valid_samples if sample.start_time.date() == target_date
    ]
    if not target_samples:
        flags.append(f"missing_{spec.metric_type}")
        latest_date = max(
            (
                sample.start_time.date()
                for sample in valid_samples
                if sample.start_time.date() <= target_date
            ),
            default=None,
        )
        if latest_date is None or (target_date - latest_date).days > stale_after_days:
            flags.append(f"stale_{spec.metric_type}")
    elif _spread(target_samples) > spec.conflict_spread:
        flags.append(f"conflicting_{spec.metric_type}")

    baseline_daily_values = _daily_values(
        valid_samples,
        earliest=target_date - dt.timedelta(days=baseline_window_days),
        latest_exclusive=target_date,
    )
    values: JsonDict = {}

    today_value = _median_sample_value(target_samples)
    if today_value is None:
        values[spec.today_key] = gap_value("insufficient_data", "missing_input", spec.unit)
    else:
        values[spec.today_key] = computed_value(today_value, spec.unit)

    baseline_value = _median_values(list(baseline_daily_values.values()))
    if len(baseline_daily_values) < min_baseline_days or baseline_value is None:
        values[spec.baseline_key] = gap_value(
            "baseline_not_established",
            "not_enough_history",
            spec.unit,
        )
        flags.append(f"baseline_not_established_{spec.metric_type}")
    else:
        values[spec.baseline_key] = computed_value(baseline_value, spec.unit)

    has_established_baseline = (
        baseline_value is not None and len(baseline_daily_values) >= min_baseline_days
    )
    if today_value is not None and has_established_baseline:
        assert baseline_value is not None
        deviation = today_value - baseline_value
        values[spec.deviation_key] = computed_value(deviation, spec.unit)
        values["deviation_pct"] = computed_value(deviation / baseline_value * 100, "percent")
    else:
        missing_reason = "missing_input" if today_value is None else "baseline_not_established"
        status: Literal["insufficient_data", "baseline_not_established"]
        status = "insufficient_data" if today_value is None else "baseline_not_established"
        values[spec.deviation_key] = gap_value(status, missing_reason, spec.unit)
        values["deviation_pct"] = gap_value(status, missing_reason, "percent")

    relevant_start = target_date - dt.timedelta(days=baseline_window_days)
    source_sample_ids = unique_ordered(
        source_id
        for sample in samples
        if relevant_start <= sample.start_time.date() <= target_date
        for source_id in _source_ids(sample)
    )
    data_quality = {
        "completeness": completeness(values),
        "flags": unique_ordered(flags),
        "input_counts": {
            "target_samples": len(target_samples),
            "baseline_days": len(baseline_daily_values),
        },
    }

    return FeatureBundle(
        status=feature_status(values),
        values=values,
        calculation_metadata=calculation_metadata(
            formula_name=spec.formula_name,
            target_date=target_date,
            parameters={
                "baseline_window_days": baseline_window_days,
                "min_baseline_days": min_baseline_days,
                "stale_after_days": stale_after_days,
                "baseline_daily_aggregate": "median",
                "baseline_aggregate": "median",
                "direction": spec.direction,
            },
        ),
        data_quality=data_quality,
        source_sample_ids=source_sample_ids,
    ).to_dict()


def _daily_values(
    samples: list[CardioSampleInput],
    *,
    earliest: dt.date,
    latest_exclusive: dt.date,
) -> dict[dt.date, float]:
    values_by_day: dict[dt.date, list[float]] = {}
    for sample in samples:
        sample_date = sample.start_time.date()
        if earliest <= sample_date < latest_exclusive:
            values_by_day.setdefault(sample_date, []).append(sample.value)
    return {
        sample_date: statistics.median(values)
        for sample_date, values in sorted(values_by_day.items())
    }


def _median_sample_value(samples: list[CardioSampleInput]) -> float | None:
    return _median_values([sample.value for sample in samples])


def _median_values(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _spread(samples: list[CardioSampleInput]) -> float:
    if len(samples) < 2:
        return 0.0
    values = [sample.value for sample in samples]
    return max(values) - min(values)


def _source_ids(sample: CardioSampleInput) -> tuple[str, ...]:
    return sample.source_sample_ids
