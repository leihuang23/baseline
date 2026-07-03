"""Adapter from synthetic fixture datasets to deterministic feature-engine inputs."""

from __future__ import annotations

import datetime as dt

from baseline_api.features.cardio import CardioSampleInput
from baseline_api.features.sleep import SleepSessionInput
from baseline_api.features.training_load import VO2SampleInput, WorkoutSessionInput
from packages.fixtures.models import FixtureDataset


def sleep_inputs(dataset: FixtureDataset) -> list[SleepSessionInput]:
    """Map fixture sleep sessions to canonical sleep feature inputs."""

    return [
        SleepSessionInput(
            start_time=session.start_time,
            end_time=session.end_time,
            duration_seconds=session.duration_seconds,
            sleep_stage_breakdown=session.stage_seconds,
            interruptions=session.interruptions,
            quality_proxy=session.quality_proxy,
            source_sample_ids=tuple(session.source_sample_ids),
        )
        for session in dataset.sleep_sessions
    ]


def hrv_inputs(dataset: FixtureDataset) -> list[CardioSampleInput]:
    """Map fixture HRV samples to canonical cardio inputs."""

    return [
        CardioSampleInput(
            sample_id=sample.sample_id,
            start_time=sample.start_time,
            value=sample.value,
            source_sample_ids=(sample.sample_id,),
        )
        for sample in dataset.samples
        if sample.metric_type == "heart_rate_variability"
    ]


def rhr_inputs(dataset: FixtureDataset) -> list[CardioSampleInput]:
    """Map fixture resting-HR samples to canonical cardio inputs."""

    return [
        CardioSampleInput(
            sample_id=sample.sample_id,
            start_time=sample.start_time,
            value=sample.value,
            source_sample_ids=(sample.sample_id,),
        )
        for sample in dataset.samples
        if sample.metric_type == "resting_heart_rate"
    ]


def workout_inputs(dataset: FixtureDataset) -> list[WorkoutSessionInput]:
    """Map fixture workouts to canonical training-load inputs."""

    return [
        WorkoutSessionInput(
            session_id=workout.workout_id,
            start_time=workout.start_time,
            end_time=workout.end_time,
            modality=workout.modality,
            duration_seconds=workout.duration_seconds,
            distance_meters=workout.distance_meters,
            active_energy_kcal=workout.active_energy_kcal,
            average_hr_bpm=workout.average_hr_bpm,
            max_hr_bpm=workout.max_hr_bpm,
            intensity_zone_distribution=workout.intensity_zone_distribution,
            perceived_exertion=workout.perceived_exertion,
            muscle_group_tags=workout.muscle_group_tags,
            source_sample_ids=tuple(workout.source_sample_ids),
        )
        for workout in dataset.workouts
    ]


def vo2_inputs(dataset: FixtureDataset) -> list[VO2SampleInput]:
    """Map fixture VO2 max samples to canonical VO2 trend inputs."""

    return [
        VO2SampleInput(
            sample_id=sample.sample_id,
            start_time=sample.start_time,
            value=sample.value,
            source_sample_ids=(sample.sample_id,),
        )
        for sample in dataset.samples
        if sample.metric_type == "vo2_max"
    ]


def target_date(dataset: FixtureDataset) -> dt.date:
    """Return the last day of the fixture as the feature-engine target date."""

    return dataset.start_date + dt.timedelta(days=dataset.days - 1)
