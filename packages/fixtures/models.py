"""Serializable synthetic fixture records."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


@dataclass(slots=True)
class HealthSample:
    """A HealthKit-like scalar sample."""

    sample_id: str
    metric_type: str
    start_time: dt.datetime
    value: float
    unit: str
    end_time: dt.datetime | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(slots=True)
class WorkoutRecord:
    """A synthetic workout session summary."""

    workout_id: str
    start_time: dt.datetime
    end_time: dt.datetime
    modality: str
    duration_seconds: float
    distance_meters: float | None
    active_energy_kcal: float
    average_hr_bpm: float
    max_hr_bpm: float
    intensity_zone_distribution: dict[str, float]
    perceived_exertion: int
    muscle_group_tags: list[str]
    source_sample_ids: list[str]


@dataclass(slots=True)
class SleepRecord:
    """A synthetic sleep session with stage durations in seconds."""

    sleep_id: str
    start_time: dt.datetime
    end_time: dt.datetime
    duration_seconds: float
    stage_seconds: dict[str, float]
    interruptions: int
    quality_proxy: float
    source_sample_ids: list[str]


@dataclass(slots=True)
class CheckInRecord:
    """A structured morning check-in with no free-text PII."""

    checkin_id: str
    date: dt.date
    energy_score: int
    mood_score: int
    soreness_score: int
    stress_score: int
    perceived_recovery_score: int
    food_quality_score: int
    alcohol_flag: bool = False
    illness_flag: bool = False
    injury_flag: bool = False
    travel_flag: bool = False
    caffeine_notes: str | None = None
    free_text_note_reference: str | None = None
    structured_notes: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(slots=True)
class FixtureDataset:
    """A complete synthetic fixture dataset."""

    name: str
    seed: int
    start_date: dt.date
    days: int
    timezone: str
    samples: list[HealthSample]
    workouts: list[WorkoutRecord]
    sleep_sessions: list[SleepRecord]
    checkins: list[CheckInRecord]
    expected_outcomes: dict[str, JsonValue] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    description: str = ""


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in sorted(value.items())}
    return value


def fixture_to_dict(dataset: FixtureDataset) -> dict[str, Any]:
    """Return a stable JSON-compatible representation of a fixture."""

    return _to_jsonable(dataset)


def fixture_to_json_bytes(dataset: FixtureDataset) -> bytes:
    """Serialize a fixture into byte-identical JSON for a given seed."""

    return json.dumps(
        fixture_to_dict(dataset),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
