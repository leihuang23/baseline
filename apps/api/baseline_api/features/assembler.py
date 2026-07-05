"""Daily feature bundle assembler for deterministic feature slices."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from baseline_api.features.cardio import (
    CardioSampleInput,
    compute_hrv_features,
    compute_rhr_features,
)
from baseline_api.features.feature_types import FEATURE_VERSION, JsonDict, rounded, unique_ordered
from baseline_api.features.goals import compute_goal_features
from baseline_api.features.recovery import compute_recovery_confidence
from baseline_api.features.sleep import SleepSessionInput, compute_sleep_features
from baseline_api.features.training_load import (
    VO2SampleInput,
    WorkoutSessionInput,
    compute_training_load_features,
    compute_vo2_features,
)


@dataclass(frozen=True, slots=True)
class DailyFeatureBundle:
    """Pure assembler output shaped for DerivedDailyFeature fields."""

    feature_version: str
    sleep_features: JsonDict
    hrv_features: JsonDict
    rhr_features: JsonDict
    data_quality: JsonDict
    anomaly_flags: list[str]
    source_sample_ids: list[str]
    computed_at: dt.datetime
    training_load_features: JsonDict
    recovery_features: JsonDict
    goal_features: JsonDict

    def to_derived_daily_feature_fields(self) -> dict[str, Any]:
        """Return fields accepted by the DerivedDailyFeature model."""

        return {
            "feature_version": self.feature_version,
            "sleep_features": self.sleep_features,
            "hrv_features": self.hrv_features,
            "rhr_features": self.rhr_features,
            "training_load_features": self.training_load_features,
            "recovery_features": self.recovery_features,
            "goal_features": self.goal_features,
            "data_quality": self.data_quality,
            "anomaly_flags": self.anomaly_flags,
            "source_sample_ids": self.source_sample_ids,
            "computed_at": self.computed_at,
        }


def assemble_daily_features(
    target_date: dt.date,
    *,
    sleep_sessions: list[SleepSessionInput],
    hrv_samples: list[CardioSampleInput],
    rhr_samples: list[CardioSampleInput],
    workouts: list[WorkoutSessionInput] | None = None,
    vo2_samples: list[VO2SampleInput] | None = None,
    daily_check_in: Mapping[str, Any] | None = None,
    personal_sleep_need_hours: float = 8.0,
    computed_at: dt.datetime | None = None,
) -> DailyFeatureBundle:
    """Assemble complete daily feature sections without performing I/O."""

    sleep_features = compute_sleep_features(
        target_date,
        sleep_sessions,
        personal_sleep_need_hours=personal_sleep_need_hours,
    )
    hrv_features = compute_hrv_features(target_date, hrv_samples)
    rhr_features = compute_rhr_features(target_date, rhr_samples)
    training_load_features = compute_training_load_features(
        target_date,
        workouts or [],
    )
    vo2_features = compute_vo2_features(target_date, vo2_samples or [])
    feature_sections = {
        "sleep": sleep_features,
        "hrv": hrv_features,
        "rhr": rhr_features,
        "training_load": training_load_features,
        "vo2": vo2_features,
    }
    completeness_by_section = {
        section: float(features["data_quality"]["completeness"])
        for section, features in feature_sections.items()
    }
    overall_completeness = rounded(
        sum(completeness_by_section.values()) / len(completeness_by_section),
        4,
    )
    flags = unique_ordered(
        flag for features in feature_sections.values() for flag in features["data_quality"]["flags"]
    )
    source_sample_ids = unique_ordered(
        source_id
        for features in feature_sections.values()
        for source_id in features["source_sample_ids"]
    )

    recovery_features = compute_recovery_confidence(
        target_date,
        section_completeness=completeness_by_section,
        flags=flags,
    )
    goal_features = compute_goal_features(
        target_date,
        vo2_samples or [],
        sleep_features=sleep_features,
        hrv_features=hrv_features,
        rhr_features=rhr_features,
        training_load_features=training_load_features,
        recovery_features=recovery_features,
        workouts=workouts or [],
        daily_check_in=daily_check_in,
        vo2_features=vo2_features,
    )

    data_quality = {
        "feature_version": FEATURE_VERSION,
        "flags": flags,
        "section_completeness": completeness_by_section,
        "overall_completeness": overall_completeness,
        "recovery_confidence_inputs": {
            "sleep_completeness": completeness_by_section["sleep"],
            "hrv_completeness": completeness_by_section["hrv"],
            "rhr_completeness": completeness_by_section["rhr"],
            "training_load_completeness": completeness_by_section["training_load"],
            "vo2_completeness": completeness_by_section["vo2"],
            "overall_completeness": overall_completeness,
            "has_stale_inputs": any(flag.startswith("stale_") for flag in flags),
            "has_missing_inputs": any(flag.startswith("missing_") for flag in flags),
            "has_conflicting_inputs": any(flag.startswith("conflicting_") for flag in flags),
            "has_anomalous_inputs": any(flag.startswith("anomalous_") for flag in flags),
        },
    }

    return DailyFeatureBundle(
        feature_version=FEATURE_VERSION,
        sleep_features=sleep_features,
        hrv_features=hrv_features,
        rhr_features=rhr_features,
        training_load_features=training_load_features,
        recovery_features=recovery_features,
        goal_features=goal_features,
        data_quality=data_quality,
        anomaly_flags=flags,
        source_sample_ids=source_sample_ids,
        computed_at=computed_at
        if computed_at is not None
        else dt.datetime.combine(target_date, dt.time(0, 0), tzinfo=dt.UTC),
    )
