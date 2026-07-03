"""Recovery confidence calculation from feature-section completeness and flags."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

from baseline_api.features.feature_types import (
    FeatureBundle,
    JsonDict,
    calculation_metadata,
    computed_value,
    gap_value,
    rounded,
    unique_ordered,
)


def compute_recovery_confidence(
    target_date: dt.date,
    *,
    section_completeness: Mapping[str, float],
    flags: list[str],
    section_weights: Mapping[str, float] | None = None,
) -> JsonDict:
    """Compute an overall recovery-confidence score from input quality.

    The score reflects completeness and consistency of all feature sections.
    It drops when inputs are missing, stale, conflicting, or anomalous.
    """

    weights = dict(section_weights) if section_weights is not None else _DEFAULT_WEIGHTS
    weighted_sum = 0.0
    weight_total = 0.0
    for section, weight in weights.items():
        weighted_sum += weight * section_completeness.get(section, 0.0)
        weight_total += weight

    base_score = weighted_sum / weight_total if weight_total > 0 else 0.0

    stale_count = sum(1 for flag in flags if flag.startswith("stale_"))
    missing_count = sum(1 for flag in flags if flag.startswith("missing_"))
    conflicting_count = sum(1 for flag in flags if flag.startswith("conflicting_"))
    anomalous_count = sum(1 for flag in flags if flag.startswith("anomalous_"))

    penalty = (
        min(0.30, stale_count * 0.15)
        + min(0.20, missing_count * 0.10)
        + min(0.20, conflicting_count * 0.10)
        + min(0.15, anomalous_count * 0.05)
    )

    score = max(0.0, min(1.0, base_score - penalty))
    level = _confidence_level(score)

    values: JsonDict = {
        "score": computed_value(score, "score_0_1", digits=4),
        "level": {
            "status": "computed",
            "value": level,
            "unit": "category",
        },
    }

    quality_flags = unique_ordered(flags)

    data_quality = {
        "completeness": rounded(score, 4),
        "flags": quality_flags,
        "input_counts": {
            "stale_flags": stale_count,
            "missing_flags": missing_count,
            "conflicting_flags": conflicting_count,
            "anomalous_flags": anomalous_count,
            "base_completeness": rounded(base_score, 4),
            "penalty": rounded(penalty, 4),
        },
    }

    return FeatureBundle(
        status="computed",
        values=values,
        calculation_metadata=calculation_metadata(
            formula_name="recovery_confidence_completeness_consistency",
            target_date=target_date,
            parameters={
                "section_weights": weights,
                "stale_penalty": 0.15,
                "missing_penalty": 0.10,
                "conflicting_penalty": 0.10,
                "anomalous_penalty": 0.05,
            },
        ),
        data_quality=data_quality,
        source_sample_ids=[],
    ).to_dict()


def compute_recovery_confidence_fallback(
    target_date: dt.date,
    reason: str,
) -> JsonDict:
    """Return an explicit recovery-confidence gap when inputs are unusable."""

    values: JsonDict = {
        "score": gap_value("insufficient_data", reason, "score_0_1"),
        "level": gap_value("insufficient_data", reason, "category"),
    }

    return FeatureBundle(
        status="insufficient_data",
        values=values,
        calculation_metadata=calculation_metadata(
            formula_name="recovery_confidence_completeness_consistency",
            target_date=target_date,
            parameters={"fallback": True, "reason": reason},
        ),
        data_quality={
            "completeness": 0.0,
            "flags": ["missing_recovery_inputs"],
            "input_counts": {},
        },
        source_sample_ids=[],
    ).to_dict()


_DEFAULT_WEIGHTS: dict[str, float] = {
    "sleep": 0.25,
    "hrv": 0.25,
    "rhr": 0.20,
    "training_load": 0.20,
    "vo2": 0.10,
}


def _confidence_level(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"
