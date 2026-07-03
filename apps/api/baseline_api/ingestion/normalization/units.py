"""Canonical-unit conversion rules per metric type.

Unknown units are rejected rather than silently coerced. All conversion
functions return ``None`` when the unit is not recognized.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from baseline_api.db.models.enums import MetricType


@dataclass(frozen=True, slots=True)
class UnitConversion:
    """Result of a unit-normalization attempt."""

    value: float
    unit: str
    confidence: float
    rejected_reason: str | None = None


_Converter = Callable[[float], tuple[float, float] | None]


def _identity(value: float) -> tuple[float, float] | None:
    return value, 1.0


def _kcal_from_kj(value: float) -> tuple[float, float] | None:
    return value * 0.239005736, 1.0


def _celsius_from_fahrenheit(value: float) -> tuple[float, float] | None:
    return (value - 32.0) * 5.0 / 9.0, 1.0


def _seconds_from_hours(value: float) -> tuple[float, float] | None:
    return value * 3600.0, 1.0


def _seconds_from_minutes(value: float) -> tuple[float, float] | None:
    return value * 60.0, 1.0


# Map each metric type to its canonical unit and accepted raw-unit aliases.
# A missing alias means the unit is unknown and will be rejected.
_CONVERSION_TABLE: dict[MetricType, dict[str, _Converter]] = {
    MetricType.heart_rate_variability: {
        "ms": _identity,
    },
    MetricType.resting_heart_rate: {
        "bpm": _identity,
        "count/min": _identity,
        "beats/min": _identity,
    },
    MetricType.steps: {
        "count": _identity,
        "steps": _identity,
    },
    MetricType.active_energy: {
        "kcal": _identity,
        "cal": _identity,
        "Cal": _identity,
        "kj": _kcal_from_kj,
        "kJ": _kcal_from_kj,
    },
    MetricType.vo2_max: {
        "mL/kg/min": _identity,
        "ml/kg/min": _identity,
    },
    MetricType.blood_oxygen: {
        "percent": _identity,
        "%": _identity,
    },
    MetricType.body_temperature: {
        "degC": _identity,
        "c": _identity,
        "celsius": _identity,
        "C": _identity,
        "degF": _celsius_from_fahrenheit,
        "f": _celsius_from_fahrenheit,
        "fahrenheit": _celsius_from_fahrenheit,
        "F": _celsius_from_fahrenheit,
    },
    MetricType.sleep_duration: {
        "s": _identity,
        "sec": _identity,
        "seconds": _identity,
        "min": _seconds_from_minutes,
        "minutes": _seconds_from_minutes,
        "h": _seconds_from_hours,
        "hr": _seconds_from_hours,
        "hours": _seconds_from_hours,
    },
    MetricType.workout: {
        "s": _identity,
        "sec": _identity,
        "seconds": _identity,
        "min": _seconds_from_minutes,
        "minutes": _seconds_from_minutes,
    },
    MetricType.other: {},  # No canonical unit; unknown units are rejected.
}


def normalize_value(
    metric_type: MetricType,
    raw_value: float,
    raw_unit: str,
) -> UnitConversion:
    """Convert a raw value to the canonical unit for its metric type.

    Returns a ``UnitConversion`` with ``confidence`` set to ``0.0`` and a
    rejection reason when the unit is not recognized. No value is ever
    fabricated: gaps remain gaps.
    """

    if not math.isfinite(raw_value):
        return UnitConversion(
            value=raw_value,
            unit=raw_unit,
            confidence=0.0,
            rejected_reason="non-finite raw value",
        )

    aliases = _CONVERSION_TABLE.get(metric_type, {})
    converter = aliases.get(raw_unit)
    if converter is None:
        return UnitConversion(
            value=raw_value,
            unit=raw_unit,
            confidence=0.0,
            rejected_reason=f"unknown unit {raw_unit!r} for {metric_type.value}",
        )

    converted = converter(raw_value)
    if converted is None:
        return UnitConversion(
            value=raw_value,
            unit=raw_unit,
            confidence=0.0,
            rejected_reason=f"conversion failed for {raw_unit!r}",
        )
    value, confidence = converted
    return UnitConversion(
        value=value,
        unit=_canonical_unit(metric_type),
        confidence=confidence,
    )


def _canonical_unit(metric_type: MetricType) -> str:
    canonical_units: dict[MetricType, str] = {
        MetricType.heart_rate_variability: "ms",
        MetricType.resting_heart_rate: "bpm",
        MetricType.steps: "count",
        MetricType.active_energy: "kcal",
        MetricType.vo2_max: "mL/kg/min",
        MetricType.blood_oxygen: "percent",
        MetricType.body_temperature: "degC",
        MetricType.sleep_duration: "s",
        MetricType.workout: "s",
        MetricType.other: "raw",
    }
    return canonical_units.get(metric_type, "raw")


def canonical_value_from_metadata(
    metadata: dict[str, Any],
    key: str,
    unit_key: str | None = None,
) -> float | None:
    """Extract an optional numeric field from source metadata.

    Returns ``None`` when the key is missing or non-numeric so that the
    normalizer never fabricates values.
    """

    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None
