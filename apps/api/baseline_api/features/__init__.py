"""Deterministic daily feature-engine calculations."""

from __future__ import annotations

from baseline_api.features.assembler import DailyFeatureBundle, assemble_daily_features
from baseline_api.features.cardio import (
    CardioSampleInput,
    compute_hrv_features,
    compute_rhr_features,
)
from baseline_api.features.feature_types import FEATURE_VERSION
from baseline_api.features.goals import compute_goal_features
from baseline_api.features.recovery import compute_recovery_confidence
from baseline_api.features.sleep import SleepSessionInput, compute_sleep_features
from baseline_api.features.training_load import (
    VO2SampleInput,
    WorkoutSessionInput,
    compute_training_load_features,
)

__all__ = [
    "FEATURE_VERSION",
    "assemble_daily_features",
    "CardioSampleInput",
    "compute_goal_features",
    "compute_hrv_features",
    "compute_recovery_confidence",
    "compute_rhr_features",
    "compute_sleep_features",
    "compute_training_load_features",
    "DailyFeatureBundle",
    "SleepSessionInput",
    "VO2SampleInput",
    "WorkoutSessionInput",
]
