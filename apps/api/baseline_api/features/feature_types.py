"""Shared JSON-ready feature types for deterministic daily calculations."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

FEATURE_VERSION = "p2-02-v1"

FeatureStatus = Literal["computed", "insufficient_data", "baseline_not_established"]
JsonDict = dict[str, Any]


def rounded(value: float, digits: int = 2) -> float:
    """Round a finite calculation result for stable JSON output."""

    if not math.isfinite(value):
        raise ValueError("feature values must be finite")
    return round(value, digits)


def computed_value(value: float, unit: str, *, digits: int = 2) -> JsonDict:
    """Return a computed feature value with a finite numeric payload."""

    return {
        "status": "computed",
        "value": rounded(value, digits),
        "unit": unit,
    }


def gap_value(status: FeatureStatus, reason: str, unit: str | None = None) -> JsonDict:
    """Return an explicit gap marker without fabricating a numeric value."""

    marker: JsonDict = {
        "status": status,
        "reason": reason,
    }
    if unit is not None:
        marker["unit"] = unit
    return marker


def calculation_metadata(
    *,
    formula_name: str,
    target_date: dt.date,
    parameters: Mapping[str, Any],
) -> JsonDict:
    """Return common deterministic calculation metadata."""

    return {
        "formula_name": formula_name,
        "formula_version": FEATURE_VERSION,
        "target_date": target_date.isoformat(),
        "deterministic": True,
        "parameters": dict(parameters),
    }


def unique_ordered(items: Iterable[str]) -> list[str]:
    """Return stable de-duplicated strings while preserving first-seen order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def completeness(values: Mapping[str, JsonDict]) -> float:
    """Return the share of requested values with computed status."""

    if not values:
        return 0.0
    computed = sum(1 for value in values.values() if value.get("status") == "computed")
    return rounded(computed / len(values), 4)


def feature_status(values: Mapping[str, JsonDict]) -> FeatureStatus:
    """Summarize value-level statuses into a feature-level status."""

    statuses = {str(value.get("status")) for value in values.values()}
    if statuses == {"computed"}:
        return "computed"
    if "insufficient_data" in statuses:
        return "insufficient_data"
    if "computed" in statuses and "baseline_not_established" in statuses:
        return "baseline_not_established"
    if "computed" in statuses:
        return "insufficient_data"
    if "baseline_not_established" in statuses:
        return "baseline_not_established"
    return "insufficient_data"


@dataclass(frozen=True, slots=True)
class FeatureBundle:
    """A serializable feature object for one deterministic formula family."""

    status: FeatureStatus
    values: JsonDict
    calculation_metadata: JsonDict
    data_quality: JsonDict
    source_sample_ids: list[str] = field(default_factory=list)
    feature_version: str = FEATURE_VERSION

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible dictionary."""

        return {
            "feature_version": self.feature_version,
            "status": self.status,
            "values": self.values,
            "calculation_metadata": self.calculation_metadata,
            "data_quality": self.data_quality,
            "source_sample_ids": self.source_sample_ids,
        }
