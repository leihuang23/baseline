"""Sleep feature calculations for the deterministic daily feature engine."""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from baseline_api.features.feature_types import (
    FeatureBundle,
    JsonDict,
    calculation_metadata,
    completeness,
    computed_value,
    feature_status,
    gap_value,
    unique_ordered,
)


@dataclass(frozen=True, slots=True)
class SleepSessionInput:
    """Canonical sleep session input for pure feature calculations."""

    start_time: dt.datetime
    end_time: dt.datetime | None
    duration_seconds: float
    sleep_stage_breakdown: dict[str, Any] = field(default_factory=dict)
    interruptions: int | None = None
    quality_proxy: float | None = None
    confidence: float = 1.0
    source_sample_ids: tuple[str, ...] = ()


def compute_sleep_features(
    target_date: dt.date,
    sessions: list[SleepSessionInput],
    *,
    personal_sleep_need_hours: float = 8.0,
    consistency_window_days: int = 7,
    min_consistency_sessions: int = 3,
    stale_after_days: int = 1,
) -> JsonDict:
    """Compute duration, debt, timing consistency, and quality proxy for one day."""

    flags: list[str] = []
    valid_sessions: list[SleepSessionInput] = []
    for session in sessions:
        if session.confidence < 1.0:
            flags.append("low_confidence_sleep")
        if not _is_finite_positive(session.duration_seconds):
            flags.append("anomalous_sleep_duration")
            continue
        if session.duration_seconds < 3 * 3600 or session.duration_seconds > 12 * 3600:
            flags.append("anomalous_sleep_duration")
            continue
        if session.end_time is None:
            flags.append("missing_sleep_end_time")
        if session.quality_proxy is not None and not 0 <= session.quality_proxy <= 1:
            flags.append("anomalous_sleep_quality_proxy")
        valid_sessions.append(session)

    target_sessions = [session for session in valid_sessions if _sleep_date(session) == target_date]
    if not target_sessions:
        flags.append("missing_sleep")
        latest_sleep_date = _latest_sleep_date(valid_sessions, target_date)
        if latest_sleep_date is None or (target_date - latest_sleep_date).days > stale_after_days:
            flags.append("stale_sleep")
    has_conflicting_target_sessions = _has_conflicting_target_sessions(target_sessions)
    if has_conflicting_target_sessions:
        flags.append("conflicting_sleep_sessions")

    values: JsonDict = {}
    if target_sessions and not has_conflicting_target_sessions:
        duration_hours = sum(session.duration_seconds for session in target_sessions) / 3600
        values["duration_hours"] = computed_value(duration_hours, "h")
        values["sleep_debt_hours"] = computed_value(
            max(0.0, personal_sleep_need_hours - duration_hours),
            "h",
        )
        quality_values = [
            session.quality_proxy
            for session in target_sessions
            if session.quality_proxy is not None and 0 <= session.quality_proxy <= 1
        ]
        if quality_values:
            values["quality_proxy"] = computed_value(
                statistics.fmean(quality_values),
                "score_0_1",
                digits=3,
            )
        else:
            values["quality_proxy"] = gap_value(
                "insufficient_data",
                "missing_quality_proxy",
                "score_0_1",
            )
    elif has_conflicting_target_sessions:
        values["duration_hours"] = gap_value("insufficient_data", "conflicting_input", "h")
        values["sleep_debt_hours"] = gap_value("insufficient_data", "conflicting_input", "h")
        values["quality_proxy"] = gap_value(
            "insufficient_data",
            "conflicting_input",
            "score_0_1",
        )
    else:
        values["duration_hours"] = gap_value("insufficient_data", "missing_input", "h")
        values["sleep_debt_hours"] = gap_value("insufficient_data", "missing_input", "h")
        values["quality_proxy"] = gap_value(
            "insufficient_data",
            "missing_input",
            "score_0_1",
        )

    consistency_sessions = _recent_sessions_for_consistency(
        valid_sessions,
        target_date,
        consistency_window_days,
    )
    if len(consistency_sessions) >= min_consistency_sessions:
        offsets = [_bedtime_offset_minutes(session.start_time) for session in consistency_sessions]
        median_offset = statistics.median(offsets)
        mean_absolute_deviation = statistics.fmean(
            abs(offset - median_offset) for offset in offsets
        )
        values["consistency_minutes"] = computed_value(mean_absolute_deviation, "min")
    else:
        values["consistency_minutes"] = gap_value(
            "baseline_not_established",
            "not_enough_sleep_history",
            "min",
        )
        flags.append("baseline_not_established_sleep_consistency")

    source_sample_ids = unique_ordered(
        source_id
        for session in sessions
        if _in_source_window(session, target_date, consistency_window_days)
        for source_id in session.source_sample_ids
    )
    data_quality = {
        "completeness": completeness(values),
        "flags": unique_ordered(flags),
        "input_counts": {
            "target_sessions": len(target_sessions),
            "consistency_sessions": len(consistency_sessions),
        },
    }

    return FeatureBundle(
        status=feature_status(values),
        values=values,
        calculation_metadata=calculation_metadata(
            formula_name="sleep_duration_debt_consistency_quality",
            target_date=target_date,
            parameters={
                "personal_sleep_need_hours": personal_sleep_need_hours,
                "consistency_window_days": consistency_window_days,
                "min_consistency_sessions": min_consistency_sessions,
                "stale_after_days": stale_after_days,
            },
        ),
        data_quality=data_quality,
        source_sample_ids=source_sample_ids,
    ).to_dict()


def _sleep_date(session: SleepSessionInput) -> dt.date | None:
    effective_end_time = _effective_end_time(session)
    if effective_end_time is None:
        return None
    return effective_end_time.date()


def _latest_sleep_date(
    sessions: list[SleepSessionInput],
    target_date: dt.date,
) -> dt.date | None:
    dates = [_sleep_date(session) for session in sessions]
    valid_dates = [date for date in dates if date is not None and date <= target_date]
    return max(valid_dates, default=None)


def _recent_sessions_for_consistency(
    sessions: list[SleepSessionInput],
    target_date: dt.date,
    window_days: int,
) -> list[SleepSessionInput]:
    earliest = target_date - dt.timedelta(days=window_days - 1)
    return [
        session
        for session in sessions
        if (sleep_date := _sleep_date(session)) is not None
        and earliest <= sleep_date <= target_date
    ]


def _bedtime_offset_minutes(start_time: dt.datetime) -> int:
    minutes = start_time.hour * 60 + start_time.minute
    if minutes < 12 * 60:
        minutes += 24 * 60
    return minutes


def _has_conflicting_target_sessions(sessions: list[SleepSessionInput]) -> bool:
    if len(sessions) <= 1:
        return False
    primary_sessions = [session for session in sessions if session.duration_seconds >= 2 * 3600]
    if len(primary_sessions) > 1:
        return True
    sorted_sessions = sorted(
        (session for session in sessions if _effective_end_time(session) is not None),
        key=lambda session: session.start_time,
    )
    return any(
        (first_end := _effective_end_time(first)) is not None and first_end > second.start_time
        for first, second in zip(sorted_sessions, sorted_sessions[1:], strict=False)
    )


def _in_source_window(session: SleepSessionInput, target_date: dt.date, window_days: int) -> bool:
    sleep_date = _sleep_date(session)
    if sleep_date is None:
        return False
    return target_date - dt.timedelta(days=window_days - 1) <= sleep_date <= target_date


def _is_finite_positive(value: float) -> bool:
    return math.isfinite(value) and value > 0


def _effective_end_time(session: SleepSessionInput) -> dt.datetime | None:
    if session.end_time is not None:
        return session.end_time
    if not _is_finite_positive(session.duration_seconds):
        return None
    return session.start_time + dt.timedelta(seconds=session.duration_seconds)
