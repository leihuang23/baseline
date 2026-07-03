"""Deterministic overlap and conflict resolution for session records.

Policy (documented and deterministic):

1. Sessions are processed by type: workouts first, then sleep.
2. Same-type overlaps: keep the longer session, drop the shorter.
   This removes obvious duplicates without fabricating data.
3. Cross-type overlaps (workout vs sleep): both sessions are retained,
   but each receives a reduced confidence score and a warning is emitted.
   No durations are fabricated or trimmed; gaps remain gaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeVar, cast
from uuid import UUID

from baseline_api.db.models.enums import Modality


@dataclass(slots=True)
class _TemporalSession:
    """Internal helper for overlap resolution."""

    start_time: datetime
    end_time: datetime | None
    duration: float
    session_type: str
    payload: object
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)


def _end_time_or_start(session: _TemporalSession) -> datetime:
    return session.end_time or session.start_time


def _overlaps(a: _TemporalSession, b: _TemporalSession) -> bool:
    a_start = a.start_time
    a_end = _end_time_or_start(a)
    b_start = b.start_time
    b_end = _end_time_or_start(b)
    return a_start < b_end and b_start < a_end


def resolve_session_conflicts(
    workouts: list[WorkoutCandidate],
    sleep_sessions: list[SleepCandidate],
    *,
    cross_type_confidence: float = 0.5,
) -> tuple[list[WorkoutCandidate], list[SleepCandidate], list[str]]:
    """Apply the documented overlap policy to workout and sleep sessions."""

    warnings: list[str] = []
    resolved_workouts = _resolve_same_type(
        [_to_temporal(w, "workout") for w in workouts],
        warnings,
    )
    resolved_sleep = _resolve_same_type(
        [_to_temporal(s, "sleep") for s in sleep_sessions],
        warnings,
    )

    for workout in resolved_workouts:
        for sleep in resolved_sleep:
            if _overlaps(workout, sleep):
                workout.confidence = min(workout.confidence, cross_type_confidence)
                sleep.confidence = min(sleep.confidence, cross_type_confidence)
                msg = (
                    f"Cross-type overlap: workout at {workout.start_time.isoformat()} "
                    f"overlaps sleep at {sleep.start_time.isoformat()}"
                )
                warnings.append(msg)
                workout.warnings.append(msg)
                sleep.warnings.append(msg)

    for workout in resolved_workouts:
        workout_payload = cast(WorkoutCandidate, workout.payload)
        workout_payload.confidence = workout.confidence
        workout_payload.warnings = workout.warnings

    for sleep in resolved_sleep:
        sleep_payload = cast(SleepCandidate, sleep.payload)
        sleep_payload.confidence = sleep.confidence
        sleep_payload.warnings = sleep.warnings

    workout_results = [cast(WorkoutCandidate, w.payload) for w in resolved_workouts]
    sleep_results = [cast(SleepCandidate, s.payload) for s in resolved_sleep]
    return workout_results, sleep_results, warnings


def _resolve_same_type(
    sessions: list[_TemporalSession],
    warnings: list[str],
) -> list[_TemporalSession]:
    """Sort by start time and keep only the longest session among any overlapping group."""

    sorted_sessions = sorted(sessions, key=lambda s: (s.start_time, -s.duration))
    kept: list[_TemporalSession] = []
    for session in sorted_sessions:
        overlapping = [keeper for keeper in kept if _overlaps(keeper, session)]
        if not overlapping:
            kept.append(session)
            continue

        longest_overlapping = max(overlapping, key=lambda k: k.duration)
        if session.duration > longest_overlapping.duration:
            for dropped in overlapping:
                warnings.append(
                    f"Dropped shorter overlapping {dropped.session_type} "
                    f"({dropped.duration:.0f}s) in favor of "
                    f"{session.duration:.0f}s session"
                )
            kept = [k for k in kept if k not in overlapping]
            kept.append(session)
        else:
            warnings.append(
                f"Dropped shorter overlapping {session.session_type} "
                f"({session.duration:.0f}s) in favor of "
                f"{longest_overlapping.duration:.0f}s session"
            )
    return kept


T = TypeVar("T")


@dataclass(slots=True)
class WorkoutCandidate:
    """Inputs for conflict resolution; mirrors WorkoutSession shape."""

    start_time: datetime
    end_time: datetime | None
    duration: float
    modality: Modality
    distance: float | None
    active_energy: float | None
    average_hr: float | None
    max_hr: float | None
    intensity_zone_distribution: dict[str, object]
    perceived_exertion: int | None
    muscle_group_tags: list[str]
    source_sample_ids: list[str]
    raw_health_sample_id: UUID
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SleepCandidate:
    """Inputs for conflict resolution; mirrors SleepSession shape."""

    start_time: datetime
    end_time: datetime | None
    duration: float
    sleep_stage_breakdown: dict[str, object]
    interruptions: int | None
    quality_proxy: float | None
    source_sample_ids: list[str]
    raw_health_sample_id: UUID
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)


def _to_temporal(session: WorkoutCandidate | SleepCandidate, session_type: str) -> _TemporalSession:
    return _TemporalSession(
        start_time=session.start_time,
        end_time=session.end_time,
        duration=session.duration,
        session_type=session_type,
        payload=session,
        confidence=session.confidence,
        warnings=list(session.warnings),
    )
