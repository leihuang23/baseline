"""Versioned golden fixtures for the deterministic feature engine.

Inputs and expected outputs live together so formula drift is caught by exact
output comparisons and by the `feature_version` change-detection test.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from baseline_api.features.cardio import CardioSampleInput
from baseline_api.features.sleep import SleepSessionInput
from baseline_api.features.training_load import VO2SampleInput, WorkoutSessionInput


@dataclass(frozen=True)
class GoldenFixture:
    """Named inputs for a single feature-engine golden case."""

    name: str
    description: str
    target_date: dt.date
    sleep_sessions: list[SleepSessionInput]
    hrv_samples: list[CardioSampleInput]
    rhr_samples: list[CardioSampleInput]
    workouts: list[WorkoutSessionInput]
    vo2_samples: list[VO2SampleInput]
    personal_sleep_need_hours: float = 8.0


def _sleep(
    start: str,
    end: str | None,
    duration_hours: float,
    quality_proxy: float | None,
    source_sample_id: str,
) -> SleepSessionInput:
    start_dt = dt.datetime.fromisoformat(start).replace(tzinfo=dt.UTC)
    end_dt = dt.datetime.fromisoformat(end).replace(tzinfo=dt.UTC) if end else None
    return SleepSessionInput(
        start_time=start_dt,
        end_time=end_dt,
        duration_seconds=round(duration_hours * 3600, 2),
        quality_proxy=quality_proxy,
        source_sample_ids=(source_sample_id,),
    )


def _hrv(day: int, value: float, sample_id: str) -> CardioSampleInput:
    return CardioSampleInput(
        sample_id=sample_id,
        start_time=dt.datetime(2026, 1, day, 6, 15, tzinfo=dt.UTC),
        value=value,
        source_sample_ids=(sample_id,),
    )


def _rhr(day: int, value: float, sample_id: str) -> CardioSampleInput:
    return CardioSampleInput(
        sample_id=sample_id,
        start_time=dt.datetime(2026, 1, day, 6, 12, tzinfo=dt.UTC),
        value=value,
        source_sample_ids=(sample_id,),
    )


def _workout(
    day: int,
    modality: str,
    duration_minutes: int,
    distance_meters: float | None,
    active_energy_kcal: float,
    average_hr_bpm: float,
    max_hr_bpm: float,
    intensity_zone_distribution: dict[str, float],
    perceived_exertion: int,
    muscle_group_tags: list[str],
    session_id: str,
) -> WorkoutSessionInput:
    duration_seconds = duration_minutes * 60
    start_time = dt.datetime(2026, 1, day, 7, 30, tzinfo=dt.UTC)
    return WorkoutSessionInput(
        session_id=session_id,
        start_time=start_time,
        end_time=start_time + dt.timedelta(seconds=duration_seconds),
        modality=modality,
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
        active_energy_kcal=active_energy_kcal,
        average_hr_bpm=average_hr_bpm,
        max_hr_bpm=max_hr_bpm,
        intensity_zone_distribution=intensity_zone_distribution,
        perceived_exertion=perceived_exertion,
        muscle_group_tags=muscle_group_tags,
        source_sample_ids=(session_id,),
    )


def _vo2(day: int, value: float, sample_id: str) -> VO2SampleInput:
    return VO2SampleInput(
        sample_id=sample_id,
        start_time=dt.datetime(2026, 1, day, 7, 10, tzinfo=dt.UTC),
        value=value,
        source_sample_ids=(sample_id,),
    )


_TARGET = dt.date(2026, 1, 20)


def _sleep_base() -> list[SleepSessionInput]:
    return [
        _sleep(
            f"2026-01-{day:02d}T22:30:00",
            f"2026-01-{day + 1:02d}T06:30:00",
            8.0,
            0.82,
            f"sleep-{day}",
        )
        for day in range(14, 21)
    ]


def _hrv_base() -> list[CardioSampleInput]:
    values = [56.0, 58.0, 57.0, 59.0, 60.0, 58.0, 61.0]
    return [_hrv(13 + index, value, f"hrv-{13 + index}") for index, value in enumerate(values)]


def _rhr_base() -> list[CardioSampleInput]:
    values = [50.0, 51.0, 50.0, 52.0, 51.0, 50.0, 49.0]
    return [_rhr(13 + index, value, f"rhr-{13 + index}") for index, value in enumerate(values)]


def _run_zones() -> dict[str, float]:
    return {"zone_1": 600.0, "zone_2": 600.0, "zone_3": 600.0}


def _workouts_base() -> list[WorkoutSessionInput]:
    zones = _run_zones()
    return [
        _workout(
            14, "run", 30, 5000.0, 300.0, 150.0, 175.0, zones, 7, ["lower_body", "cardio"], "w-14"
        ),
        _workout(
            16, "run", 30, 5000.0, 300.0, 150.0, 175.0, zones, 7, ["lower_body", "cardio"], "w-16"
        ),
        _workout(
            18, "run", 30, 5000.0, 300.0, 150.0, 175.0, zones, 7, ["lower_body", "cardio"], "w-18"
        ),
        _workout(
            20, "run", 30, 5000.0, 300.0, 150.0, 175.0, zones, 7, ["lower_body", "cardio"], "w-20"
        ),
    ]


def _vo2_base() -> list[VO2SampleInput]:
    return [_vo2(6, 45.0, "vo2-6"), _vo2(13, 45.5, "vo2-13"), _vo2(20, 46.0, "vo2-20")]


FIXTURES: dict[str, GoldenFixture] = {
    "normal_day": GoldenFixture(
        name="normal_day",
        description="Typical day with complete sleep, cardio, training, and VO2 inputs.",
        target_date=_TARGET,
        sleep_sessions=_sleep_base(),
        hrv_samples=_hrv_base() + [_hrv(20, 62.0, "hrv-20a"), _hrv(20, 64.0, "hrv-20b")],
        rhr_samples=_rhr_base() + [_rhr(20, 49.0, "rhr-20a"), _rhr(20, 51.0, "rhr-20b")],
        workouts=_workouts_base(),
        vo2_samples=_vo2_base(),
    ),
    "missing_hrv": GoldenFixture(
        name="missing_hrv",
        description="HRV samples are absent; the engine must report gaps, not fabricate values.",
        target_date=_TARGET,
        sleep_sessions=_sleep_base(),
        hrv_samples=[],
        rhr_samples=_rhr_base() + [_rhr(20, 49.0, "rhr-20a"), _rhr(20, 51.0, "rhr-20b")],
        workouts=_workouts_base(),
        vo2_samples=_vo2_base(),
    ),
    "missing_sleep": GoldenFixture(
        name="missing_sleep",
        description="Sleep sessions are absent; sleep features must be explicit gaps.",
        target_date=_TARGET,
        sleep_sessions=[],
        hrv_samples=_hrv_base() + [_hrv(20, 62.0, "hrv-20a"), _hrv(20, 64.0, "hrv-20b")],
        rhr_samples=_rhr_base() + [_rhr(20, 49.0, "rhr-20a"), _rhr(20, 51.0, "rhr-20b")],
        workouts=_workouts_base(),
        vo2_samples=_vo2_base(),
    ),
    "missing_rhr": GoldenFixture(
        name="missing_rhr",
        description="Resting heart rate samples are absent.",
        target_date=_TARGET,
        sleep_sessions=_sleep_base(),
        hrv_samples=_hrv_base() + [_hrv(20, 62.0, "hrv-20a"), _hrv(20, 64.0, "hrv-20b")],
        rhr_samples=[],
        workouts=_workouts_base(),
        vo2_samples=_vo2_base(),
    ),
    "missing_training_load": GoldenFixture(
        name="missing_training_load",
        description="No workouts are present; load features must be insufficient_data.",
        target_date=_TARGET,
        sleep_sessions=_sleep_base(),
        hrv_samples=_hrv_base() + [_hrv(20, 62.0, "hrv-20a"), _hrv(20, 64.0, "hrv-20b")],
        rhr_samples=_rhr_base() + [_rhr(20, 49.0, "rhr-20a"), _rhr(20, 51.0, "rhr-20b")],
        workouts=[],
        vo2_samples=_vo2_base(),
    ),
    "missing_vo2": GoldenFixture(
        name="missing_vo2",
        description="VO2 samples are absent; goal/VO2 features must be explicit gaps.",
        target_date=_TARGET,
        sleep_sessions=_sleep_base(),
        hrv_samples=_hrv_base() + [_hrv(20, 62.0, "hrv-20a"), _hrv(20, 64.0, "hrv-20b")],
        rhr_samples=_rhr_base() + [_rhr(20, 49.0, "rhr-20a"), _rhr(20, 51.0, "rhr-20b")],
        workouts=_workouts_base(),
        vo2_samples=[],
    ),
    "stale_data": GoldenFixture(
        name="stale_data",
        description="Sleep and HRV data are older than the stale threshold.",
        target_date=_TARGET,
        sleep_sessions=[
            _sleep(
                "2026-01-16T22:30:00",
                "2026-01-17T06:30:00",
                8.0,
                0.82,
                "sleep-stale",
            )
        ],
        hrv_samples=[_hrv(16, 58.0, "hrv-stale")],
        rhr_samples=[_rhr(20, 50.0, "rhr-today")],
        workouts=_workouts_base(),
        vo2_samples=_vo2_base(),
    ),
    "anomalous_spike": GoldenFixture(
        name="anomalous_spike",
        description="Out-of-range samples are flagged; valid data still computes where possible.",
        target_date=_TARGET,
        sleep_sessions=_sleep_base()
        + [
            _sleep(
                "2026-01-19T22:30:00",
                "2026-01-20T16:30:00",
                18.0,
                0.5,
                "sleep-anomalous",
            )
        ],
        hrv_samples=_hrv_base()
        + [
            _hrv(20, 62.0, "hrv-20a"),
            _hrv(20, 64.0, "hrv-20b"),
            _hrv(20, 300.0, "hrv-anomalous"),
        ],
        rhr_samples=_rhr_base()
        + [
            _rhr(20, 49.0, "rhr-20a"),
            _rhr(20, 51.0, "rhr-20b"),
            _rhr(20, 250.0, "rhr-anomalous"),
        ],
        workouts=_workouts_base()
        + [
            _workout(
                20,
                "run",
                30,
                5000.0,
                300.0,
                250.0,
                270.0,
                _run_zones(),
                7,
                ["lower_body", "cardio"],
                "w-anomalous",
            )
        ],
        vo2_samples=_vo2_base(),
    ),
    "conflicting_samples": GoldenFixture(
        name="conflicting_samples",
        description="Conflicting same-day samples produce flags and suppress fabricated values.",
        target_date=_TARGET,
        sleep_sessions=_sleep_base()
        + [
            _sleep(
                "2026-01-19T22:00:00",
                "2026-01-20T06:00:00",
                8.0,
                0.8,
                "sleep-conflict-a",
            ),
            _sleep(
                "2026-01-19T23:00:00",
                "2026-01-20T07:00:00",
                8.0,
                0.85,
                "sleep-conflict-b",
            ),
        ],
        hrv_samples=_hrv_base()
        + [
            _hrv(20, 45.0, "hrv-20a"),
            _hrv(20, 85.0, "hrv-20b"),
        ],
        rhr_samples=_rhr_base()
        + [
            _rhr(20, 55.0, "rhr-20a"),
            _rhr(20, 95.0, "rhr-20b"),
        ],
        workouts=_workouts_base(),
        vo2_samples=_vo2_base(),
    ),
    "high_density_training": GoldenFixture(
        name="high_density_training",
        description="Three lower-body strength sessions in six days plus a target-day run.",
        target_date=_TARGET,
        sleep_sessions=_sleep_base(),
        hrv_samples=_hrv_base() + [_hrv(20, 62.0, "hrv-20a"), _hrv(20, 64.0, "hrv-20b")],
        rhr_samples=_rhr_base() + [_rhr(20, 49.0, "rhr-20a"), _rhr(20, 51.0, "rhr-20b")],
        workouts=[
            _workout(
                15,
                "strength_training",
                45,
                None,
                250.0,
                125.0,
                155.0,
                {"zone_1": 300.0, "zone_2": 600.0, "zone_3": 900.0, "zone_4": 300.0},
                8,
                ["lower_body"],
                "w-15",
            ),
            _workout(
                17,
                "strength_training",
                45,
                None,
                250.0,
                125.0,
                155.0,
                {"zone_1": 300.0, "zone_2": 600.0, "zone_3": 900.0, "zone_4": 300.0},
                8,
                ["lower_body"],
                "w-17",
            ),
            _workout(
                19,
                "strength_training",
                45,
                None,
                250.0,
                125.0,
                155.0,
                {"zone_1": 300.0, "zone_2": 600.0, "zone_3": 900.0, "zone_4": 300.0},
                8,
                ["lower_body"],
                "w-19",
            ),
            _workout(
                20,
                "run",
                30,
                5000.0,
                300.0,
                150.0,
                175.0,
                _run_zones(),
                7,
                ["lower_body", "cardio"],
                "w-20",
            ),
        ],
        vo2_samples=_vo2_base(),
    ),
    "vo2_improving_recovery_declining": GoldenFixture(
        name="vo2_improving_recovery_declining",
        description="VO2 max trend improves while acute recovery signals decline.",
        target_date=_TARGET,
        sleep_sessions=[
            _sleep(
                "2026-01-16T23:30:00",
                "2026-01-17T05:30:00",
                6.0,
                0.6,
                "sleep-degraded",
            )
        ],
        hrv_samples=[_hrv(13, 56.0, "hrv-13"), _hrv(14, 58.0, "hrv-14"), _hrv(15, 57.0, "hrv-15")],
        rhr_samples=_rhr_base() + [_rhr(20, 58.0, "rhr-20a"), _rhr(20, 60.0, "rhr-20b")],
        workouts=_workouts_base(),
        vo2_samples=[_vo2(6, 44.0, "vo2-6"), _vo2(13, 45.0, "vo2-13"), _vo2(20, 46.5, "vo2-20")],
        personal_sleep_need_hours=8.0,
    ),
}


def _jsonify_datetime(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonify_datetime(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify_datetime(v) for v in value]
    return value


def load_expected_outputs() -> dict[str, dict[str, Any]]:
    """Load the exact expected feature bundles for every golden fixture."""

    path = Path(__file__).with_suffix("").parent / "fixtures" / "expected_golden_outputs.json"
    return json.loads(path.read_text(encoding="utf-8"))
