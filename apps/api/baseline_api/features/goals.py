"""Goal-relevant indicator hooks for deterministic feature assembly."""

from __future__ import annotations

import datetime as dt

from baseline_api.features.feature_types import (
    FeatureBundle,
    JsonDict,
    calculation_metadata,
    completeness,
    unique_ordered,
)
from baseline_api.features.training_load import VO2SampleInput, compute_vo2_features


def compute_goal_features(
    target_date: dt.date,
    vo2_samples: list[VO2SampleInput],
    *,
    active_goal_categories: list[str] | None = None,
) -> JsonDict:
    """Compute goal-relevant indicators that Phase 5 goal modules can extend."""

    vo2_features = compute_vo2_features(target_date, vo2_samples)
    categories = active_goal_categories or [
        "vo2_max",
        "strength",
        "recovery",
        "sleep",
        "cognitive_performance",
    ]

    values: JsonDict = {
        "vo2_trend": vo2_features,
        "goal_hooks": {
            "status": "computed",
            "value": {
                "active_categories": unique_ordered(categories),
                "extension_points": [
                    "strength_progression",
                    "cognitive_load_proxy",
                    "long_term_wellness",
                ],
            },
            "unit": "structured",
        },
    }

    source_sample_ids = list(vo2_features.get("source_sample_ids", []))

    data_quality = {
        "completeness": completeness(values),
        "flags": list(vo2_features.get("data_quality", {}).get("flags", [])),
        "input_counts": {
            "active_goal_categories": len(categories),
            "vo2_samples": vo2_features.get("data_quality", {})
            .get("input_counts", {})
            .get("window_samples", 0),
        },
    }

    return FeatureBundle(
        status=vo2_features["status"],
        values=values,
        calculation_metadata=calculation_metadata(
            formula_name="goal_indicator_hooks",
            target_date=target_date,
            parameters={
                "active_goal_categories": categories,
                "extension_points": values["goal_hooks"]["value"]["extension_points"],
            },
        ),
        data_quality=data_quality,
        source_sample_ids=source_sample_ids,
    ).to_dict()
