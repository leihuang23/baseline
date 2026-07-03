"""Deterministic synthetic physiology generators."""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, uuid5

from packages.fixtures.models import (
    CheckInRecord,
    FixtureDataset,
    HealthSample,
    SleepRecord,
    WorkoutRecord,
)

UTC = dt.UTC
SYNTHETIC_DEVICE = "Baseline Synthetic Watch"
SYNTHETIC_PLATFORM = "apple_health_synthetic"


@dataclass(frozen=True, slots=True)
class PersonaConfig:
    """Configuration for a deterministic synthetic persona."""

    seed: int = 42
    start_date: dt.date = dt.date(2026, 1, 5)
    days: int = 60
    name: str = "demo_60_day_persona"
    timezone: str = "UTC"
    hrv_baseline_ms: float = 58.0
    resting_hr_baseline_bpm: float = 54.0
    sleep_target_hours: float = 7.75
    vo2_max_baseline: float = 47.0
    step_baseline: int = 8500
    training_bias: float = 1.0
    perturbations: dict[int, str] = field(default_factory=dict)
    expected_outcomes: dict[str, object] = field(default_factory=dict)
    labels: tuple[str, ...] = ()
    description: str = ""


def generate_persona_dataset(config: PersonaConfig) -> FixtureDataset:
    """Generate multi-day synthetic data for a single persona."""

    rng = random.Random(config.seed)
    samples: list[HealthSample] = []
    workouts: list[WorkoutRecord] = []
    sleep_sessions: list[SleepRecord] = []
    checkins: list[CheckInRecord] = []
    sleep_debt_hours = 0.0

    for day_index in range(config.days):
        day = config.start_date + dt.timedelta(days=day_index)
        perturbation = config.perturbations.get(day_index)
        workout_plan = _workout_plan(day, config.training_bias, perturbation)
        sleep_hours = _sleep_hours(config, rng, workout_plan, perturbation, sleep_debt_hours)
        sleep_debt_hours = max(0.0, sleep_debt_hours + config.sleep_target_hours - sleep_hours)

        day_samples: list[HealthSample] = []
        sleep = _sleep_record(config, day_index, day, sleep_hours, rng, perturbation)
        if perturbation != "stale_sleep":
            sleep_sessions.append(sleep)
            day_samples.append(
                _sample(
                    config,
                    day_index,
                    "sleep_duration",
                    sleep.start_time,
                    round(sleep.duration_seconds / 3600, 2),
                    "h",
                    end_time=sleep.end_time,
                    suffix="sleep-duration",
                )
            )

        recovery_penalty = min(16.0, sleep_debt_hours * 2.2)
        illness_penalty = 14.0 if perturbation == "illness" else 0.0
        travel_penalty = 7.0 if perturbation == "travel" else 0.0
        hrv = _bounded(
            config.hrv_baseline_ms
            + rng.gauss(0, 3.5)
            - recovery_penalty
            - illness_penalty
            - travel_penalty,
            18,
            105,
        )
        rhr = _bounded(
            config.resting_hr_baseline_bpm
            + rng.gauss(0, 1.8)
            + recovery_penalty * 0.42
            + illness_penalty * 0.55
            + travel_penalty * 0.35,
            38,
            95,
        )
        sample_time = dt.datetime.combine(day, dt.time(6, 15), tzinfo=UTC)
        if perturbation != "missing_hrv":
            day_samples.append(
                _sample(
                    config,
                    day_index,
                    "heart_rate_variability",
                    sample_time,
                    round(hrv, 1),
                    "ms",
                )
            )
        day_samples.append(
            _sample(
                config,
                day_index,
                "resting_heart_rate",
                sample_time - dt.timedelta(minutes=3),
                round(rhr, 1),
                "count/min",
            )
        )

        steps = _steps(config, rng, workout_plan, perturbation)
        day_samples.append(
            _sample(
                config,
                day_index,
                "steps",
                dt.datetime.combine(day, dt.time(21, 0), tzinfo=UTC),
                steps,
                "count",
            )
        )
        day_samples.append(
            _sample(
                config,
                day_index,
                "vo2_max",
                dt.datetime.combine(day, dt.time(7, 10), tzinfo=UTC),
                _vo2_value(config, rng, day_index, perturbation),
                "mL/kg/min",
            )
        )

        if workout_plan is not None:
            workouts.append(_workout_record(config, day_index, day, rng, workout_plan))

        samples.extend(day_samples)
        checkins.append(
            _checkin_record(
                config=config,
                day_index=day_index,
                day=day,
                rng=rng,
                sleep_hours=sleep_hours,
                sleep_debt_hours=sleep_debt_hours,
                workout_plan=workout_plan,
                perturbation=perturbation,
            )
        )

    return FixtureDataset(
        name=config.name,
        seed=config.seed,
        start_date=config.start_date,
        days=config.days,
        timezone=config.timezone,
        samples=samples,
        workouts=workouts,
        sleep_sessions=sleep_sessions,
        checkins=checkins,
        expected_outcomes=dict(config.expected_outcomes),
        labels=list(config.labels),
        description=config.description,
    )


def _sample(
    config: PersonaConfig,
    day_index: int,
    metric_type: str,
    start_time: dt.datetime,
    value: float,
    unit: str,
    *,
    end_time: dt.datetime | None = None,
    suffix: str | None = None,
) -> HealthSample:
    sample_id = _stable_id(config.name, config.seed, day_index, metric_type, suffix or "sample")
    return HealthSample(
        sample_id=sample_id,
        metric_type=metric_type,
        start_time=start_time,
        end_time=end_time,
        value=value,
        unit=unit,
        metadata={
            "source_platform": SYNTHETIC_PLATFORM,
            "source_device": SYNTHETIC_DEVICE,
            "synthetic": True,
        },
    )


def _sleep_record(
    config: PersonaConfig,
    day_index: int,
    day: dt.date,
    sleep_hours: float,
    rng: random.Random,
    perturbation: str | None,
) -> SleepRecord:
    bedtime = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(22, 35), tzinfo=UTC)
    bedtime += dt.timedelta(minutes=int(rng.gauss(0, 24)))
    duration_seconds = round(sleep_hours * 3600)
    end_time = bedtime + dt.timedelta(seconds=duration_seconds)
    deep = duration_seconds * _bounded(0.17 + rng.gauss(0, 0.015), 0.08, 0.25)
    rem = duration_seconds * _bounded(0.22 + rng.gauss(0, 0.018), 0.12, 0.32)
    awake = duration_seconds * _bounded(0.045 + rng.gauss(0, 0.012), 0.02, 0.12)
    core = max(0.0, duration_seconds - deep - rem - awake)
    quality = _bounded(
        (sleep_hours - 4.8) / 3.5 - (0.12 if perturbation == "travel" else 0),
        0.05,
        0.98,
    )
    return SleepRecord(
        sleep_id=_stable_id(config.name, config.seed, day_index, "sleep", "session"),
        start_time=bedtime,
        end_time=end_time,
        duration_seconds=float(duration_seconds),
        stage_seconds={
            "awake": round(awake, 1),
            "core": round(core, 1),
            "deep": round(deep, 1),
            "rem": round(rem, 1),
        },
        interruptions=max(0, int(rng.gauss(1.2, 1.1)) + (2 if perturbation == "travel" else 0)),
        quality_proxy=round(quality, 3),
        source_sample_ids=[
            _stable_id(config.name, config.seed, day_index, "sleep_duration", "sleep-duration")
        ],
    )


def _workout_record(
    config: PersonaConfig,
    day_index: int,
    day: dt.date,
    rng: random.Random,
    plan: str,
) -> WorkoutRecord:
    start = dt.datetime.combine(day, dt.time(7, 30), tzinfo=UTC) + dt.timedelta(
        minutes=int(rng.gauss(0, 18))
    )
    if plan == "run":
        duration = round(_bounded(rng.gauss(2700, 420), 1500, 4800), 1)
        distance = round(duration / 60 * _bounded(rng.gauss(150, 14), 115, 190), 1)
        energy = round(duration / 60 * _bounded(rng.gauss(10.2, 1.1), 7.0, 14.0), 1)
        avg_hr = round(_bounded(rng.gauss(142, 8), 110, 172), 1)
        max_hr = round(_bounded(avg_hr + rng.gauss(28, 6), avg_hr + 8, 196), 1)
        zones = {"z1": 0.1, "z2": 0.48, "z3": 0.29, "z4": 0.11, "z5": 0.02}
        rpe = int(_bounded(round(rng.gauss(6, 1)), 3, 9))
        tags = ["cardio", "lower_body"]
    else:
        duration = round(_bounded(rng.gauss(2400, 360), 1200, 4200), 1)
        distance = None
        energy = round(duration / 60 * _bounded(rng.gauss(7.5, 1.2), 4.5, 12.0), 1)
        avg_hr = round(_bounded(rng.gauss(126, 9), 95, 165), 1)
        max_hr = round(_bounded(avg_hr + rng.gauss(25, 7), avg_hr + 8, 190), 1)
        zones = {"z1": 0.24, "z2": 0.42, "z3": 0.23, "z4": 0.09, "z5": 0.02}
        rpe = int(_bounded(round(rng.gauss(7, 1)), 4, 10))
        tags = ["strength", "lower_body"] if plan == "lower_strength" else ["strength", "full_body"]

    workout_id = _stable_id(config.name, config.seed, day_index, "workout", plan)
    return WorkoutRecord(
        workout_id=workout_id,
        start_time=start,
        end_time=start + dt.timedelta(seconds=duration),
        modality="run" if plan == "run" else ("kettlebell" if plan == "kettlebell" else "strength"),
        duration_seconds=duration,
        distance_meters=distance,
        active_energy_kcal=energy,
        average_hr_bpm=avg_hr,
        max_hr_bpm=max_hr,
        intensity_zone_distribution=zones,
        perceived_exertion=rpe,
        muscle_group_tags=tags,
        source_sample_ids=[workout_id],
    )


def _checkin_record(
    *,
    config: PersonaConfig,
    day_index: int,
    day: dt.date,
    rng: random.Random,
    sleep_hours: float,
    sleep_debt_hours: float,
    workout_plan: str | None,
    perturbation: str | None,
) -> CheckInRecord:
    recovery = int(_bounded(round(8 - sleep_debt_hours * 0.75 + rng.gauss(0, 0.8)), 1, 10))
    energy = int(
        _bounded(
            round(7 + (sleep_hours - config.sleep_target_hours) * 0.8 + rng.gauss(0, 0.9)),
            1,
            10,
        )
    )
    soreness_base = 6 if workout_plan in {"lower_strength", "kettlebell"} else 3
    soreness = int(_bounded(round(soreness_base + rng.gauss(0, 1)), 1, 10))
    stress = int(
        _bounded(round(4 + rng.gauss(0, 1.2) + (2 if perturbation == "travel" else 0)), 1, 10)
    )
    if perturbation == "illness":
        recovery = min(recovery, 4)
        energy = min(energy, 5)
    return CheckInRecord(
        checkin_id=_stable_id(config.name, config.seed, day_index, "checkin", "morning"),
        date=day,
        energy_score=energy,
        mood_score=int(_bounded(round(7 + rng.gauss(0, 0.9)), 1, 10)),
        soreness_score=soreness,
        stress_score=stress,
        perceived_recovery_score=recovery,
        food_quality_score=int(_bounded(round(7 + rng.gauss(0, 1)), 1, 10)),
        alcohol_flag=day.weekday() == 5 and rng.random() < 0.2,
        illness_flag=perturbation == "illness",
        injury_flag=False,
        travel_flag=perturbation == "travel",
        caffeine_notes="normal synthetic intake",
        structured_notes={
            "synthetic": True,
            "motivation": "high" if energy >= 7 else "moderate",
            "priority": "cognitive_work" if perturbation == "cognitive_priority" else "training",
        },
    )


def _workout_plan(day: dt.date, training_bias: float, perturbation: str | None) -> str | None:
    if perturbation == "illness":
        return None
    weekday = day.weekday()
    if perturbation == "lower_body_cluster" and weekday in {0, 2, 5}:
        return "lower_strength"
    if weekday in {1, 5}:
        return "run"
    if weekday == 3:
        return "kettlebell"
    if weekday == 0 and training_bias >= 1.1:
        return "lower_strength"
    return None


def _sleep_hours(
    config: PersonaConfig,
    rng: random.Random,
    workout_plan: str | None,
    perturbation: str | None,
    sleep_debt_hours: float,
) -> float:
    base = config.sleep_target_hours + rng.gauss(0, 0.38)
    if workout_plan in {"lower_strength", "kettlebell"}:
        base -= 0.18
    if perturbation == "sleep_debt":
        base -= 1.65
    if perturbation == "travel":
        base -= 1.05
    if perturbation == "illness":
        base -= 0.6
    if sleep_debt_hours > 3.5 and perturbation not in {"sleep_debt", "travel"}:
        base += 0.45
    return round(_bounded(base, 4.2, 9.4), 2)


def _steps(
    config: PersonaConfig,
    rng: random.Random,
    workout_plan: str | None,
    perturbation: str | None,
) -> int:
    steps = config.step_baseline + int(rng.gauss(0, 1200))
    if workout_plan == "run":
        steps += int(rng.gauss(5200, 800))
    if workout_plan in {"kettlebell", "lower_strength"}:
        steps += int(rng.gauss(1500, 600))
    if perturbation == "illness":
        steps -= 4200
    if perturbation == "travel":
        steps += 2500
    return int(_bounded(steps, 1800, 26000))


def _vo2_value(
    config: PersonaConfig,
    rng: random.Random,
    day_index: int,
    perturbation: str | None,
) -> float:
    trend = day_index * 0.035
    if perturbation == "vo2_improving":
        trend = day_index * 0.09
    penalty = -0.25 if perturbation in {"illness", "sleep_debt"} else 0
    return round(
        _bounded(config.vo2_max_baseline + trend + rng.gauss(0, 0.22) + penalty, 25, 70),
        1,
    )


def _bounded(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _stable_id(*parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(str(part) for part in parts)))
