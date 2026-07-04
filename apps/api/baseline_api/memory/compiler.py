"""Deterministic personal memory compilers."""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from baseline_api.db.models.assessment import ReadinessAssessment, Recommendation
from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.enums import ConfidenceLevel, PeriodType
from baseline_api.db.models.features import DerivedDailyFeature
from baseline_api.db.models.memory import MemorySummary

MEMORY_SUMMARY_VERSION = "memory-summary-v1"

JsonDict = dict[str, Any]

_CONFIDENCE_SCORES: dict[ConfidenceLevel | str, float] = {
    ConfidenceLevel.high: 0.9,
    ConfidenceLevel.medium: 0.7,
    ConfidenceLevel.low: 0.45,
    "high": 0.9,
    "medium": 0.7,
    "low": 0.45,
}

_SENSITIVE_CHECKIN_FIELDS = [
    "daily_check_in.caffeine_notes",
    "daily_check_in.structured_notes",
    "daily_check_in.free_text_note_reference",
    "daily_check_in.free_text_note_summary",
]

_RISK_HYPOTHESES = {
    "high_sleep_debt": "Sleep debt may be limiting recovery readiness.",
    "high_training_density": "Recent training density may be constraining recovery.",
    "elevated_rhr": "Elevated resting heart rate may reflect incomplete recovery.",
    "high_soreness": "Subjective soreness may be constraining training tolerance.",
    "poor_subjective_recovery": (
        "Poor subjective recovery may be constraining training tolerance."
    ),
    "conflicting_signals": "Mixed readiness signals may require conservative interpretation.",
    "hard_safety_illness": "Recent illness may disrupt normal readiness baselines.",
    "hard_safety_injury": "Recent injury may disrupt normal readiness baselines.",
}

_TREND_METRIC_KEYS = ("vo2_max", "sleep_debt_hours")


class MemoryCompiler:
    """Build versioned, source-linked memory summaries without LLM invention."""

    def compile_daily(
        self,
        *,
        user_id: UUID,
        feature: DerivedDailyFeature,
        assessment: ReadinessAssessment,
        recommendation: Recommendation | None = None,
        checkin: DailyCheckIn | None = None,
        include_sensitive_notes: bool = False,
    ) -> MemorySummary:
        """Compile one day of structured memory from deterministic artifacts."""

        if feature.user_id != user_id or assessment.user_id != user_id:
            raise ValueError("daily memory inputs must belong to user_id")
        if feature.date != assessment.date:
            raise ValueError("feature and assessment dates must match")
        if recommendation is not None and recommendation.user_id != user_id:
            raise ValueError("recommendation must belong to user_id")
        if checkin is not None and checkin.user_id != user_id:
            raise ValueError("checkin must belong to user_id")

        confidence = _confidence_score(assessment.confidence)
        observations = self._daily_observations(
            feature=feature,
            assessment=assessment,
            recommendation=recommendation,
            checkin=checkin,
            confidence=confidence,
        )
        hypotheses = self._daily_hypotheses(assessment=assessment, confidence=confidence)
        source_refs = _unique_refs(
            [
                *_flatten_item_refs(observations),
                *_flatten_item_refs(hypotheses),
                *_source_sample_refs(feature),
            ]
        )

        return MemorySummary(
            user_id=user_id,
            period_type=PeriodType.daily,
            start_date=feature.date,
            end_date=feature.date,
            summary_version=MEMORY_SUMMARY_VERSION,
            observations=observations,
            hypotheses=hypotheses,
            confidence=_aggregate_confidence(observations, hypotheses),
            source_refs=source_refs,
            sensitive_fields_excluded=(
                []
                if include_sensitive_notes or checkin is None
                else list(_SENSITIVE_CHECKIN_FIELDS)
            ),
        )

    def compile_weekly(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
        daily_summaries: Sequence[MemorySummary],
    ) -> MemorySummary:
        """Compile a weekly summary from existing daily memory records."""

        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        if not daily_summaries:
            raise ValueError("daily_summaries must not be empty")

        dailies = sorted(daily_summaries, key=lambda item: item.start_date)
        for summary in dailies:
            if summary.user_id != user_id:
                raise ValueError("daily summary must belong to user_id")
            if summary.period_type != PeriodType.daily:
                raise ValueError("weekly compiler only accepts daily summaries")
            if summary.start_date < start_date or summary.end_date > end_date:
                raise ValueError("daily summary is outside weekly period")

        daily_refs = [_memory_summary_ref(summary) for summary in dailies]
        daily_source_refs = [ref for row in dailies for ref in row.source_refs]
        preserved_refs = _unique_refs([*daily_refs, *daily_source_refs])
        observations = self._weekly_observations(dailies=dailies, source_refs=preserved_refs)
        hypotheses = self._weekly_hypotheses(dailies=dailies, source_refs=preserved_refs)

        return MemorySummary(
            user_id=user_id,
            period_type=PeriodType.weekly,
            start_date=start_date,
            end_date=end_date,
            summary_version=MEMORY_SUMMARY_VERSION,
            observations=observations,
            hypotheses=hypotheses,
            confidence=_aggregate_confidence(observations, hypotheses),
            source_refs=preserved_refs,
            sensitive_fields_excluded=_unique_strings(
                field for summary in dailies for field in summary.sensitive_fields_excluded
            ),
        )

    def compile_monthly(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
        daily_summaries: Sequence[MemorySummary],
        weekly_summaries: Sequence[MemorySummary] = (),
    ) -> MemorySummary:
        """Compile a monthly summary from daily records and in-period weekly summaries."""

        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        if not daily_summaries and not weekly_summaries:
            raise ValueError("monthly compiler requires daily or weekly summaries")

        dailies = sorted(daily_summaries, key=lambda item: item.start_date)
        weeklies = sorted(weekly_summaries, key=lambda item: item.start_date)
        _validate_compaction_inputs(
            dailies,
            user_id=user_id,
            period_type=PeriodType.daily,
            start_date=start_date,
            end_date=end_date,
            label="daily",
        )
        _validate_compaction_inputs(
            weeklies,
            user_id=user_id,
            period_type=PeriodType.weekly,
            start_date=start_date,
            end_date=end_date,
            label="weekly",
        )

        dailies, weeklies = _reconcile_monthly_inputs(dailies=dailies, weeklies=weeklies)
        lower_summaries = [*dailies, *weeklies]
        preserved_refs = _summary_source_refs(lower_summaries)
        observations = self._monthly_observations(
            dailies=dailies,
            weeklies=weeklies,
            start_date=start_date,
            end_date=end_date,
            source_refs=preserved_refs,
        )
        hypotheses = self._monthly_hypotheses(
            dailies=dailies,
            weeklies=weeklies,
            source_refs=preserved_refs,
        )

        return MemorySummary(
            user_id=user_id,
            period_type=PeriodType.monthly,
            start_date=start_date,
            end_date=end_date,
            summary_version=MEMORY_SUMMARY_VERSION,
            observations=observations,
            hypotheses=hypotheses,
            confidence=_aggregate_confidence(observations, hypotheses),
            source_refs=preserved_refs,
            sensitive_fields_excluded=_unique_strings(
                field for summary in lower_summaries for field in summary.sensitive_fields_excluded
            ),
        )

    def compile_quarterly(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
        monthly_summaries: Sequence[MemorySummary],
    ) -> MemorySummary:
        """Compile a quarterly summary from monthly memory summaries."""

        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        if not monthly_summaries:
            raise ValueError("monthly_summaries must not be empty")

        monthlies = sorted(monthly_summaries, key=lambda item: item.start_date)
        _validate_compaction_inputs(
            monthlies,
            user_id=user_id,
            period_type=PeriodType.monthly,
            start_date=start_date,
            end_date=end_date,
            label="monthly",
        )
        _validate_calendar_quarter_period(start_date=start_date, end_date=end_date)
        _validate_contiguous_coverage(
            monthlies,
            start_date=start_date,
            end_date=end_date,
            label="monthly",
        )
        _validate_calendar_monthly_inputs(
            monthlies,
            start_date=start_date,
            end_date=end_date,
        )

        monthly_refs = [_memory_summary_ref(summary) for summary in monthlies]
        monthly_source_refs = [ref for row in monthlies for ref in row.source_refs]
        preserved_refs = _unique_refs([*monthly_refs, *monthly_source_refs])
        observations = self._quarterly_observations(
            monthlies=monthlies,
            start_date=start_date,
            end_date=end_date,
            source_refs=preserved_refs,
        )
        hypotheses = self._quarterly_hypotheses(
            monthlies=monthlies,
            source_refs=preserved_refs,
        )

        return MemorySummary(
            user_id=user_id,
            period_type=PeriodType.quarterly,
            start_date=start_date,
            end_date=end_date,
            summary_version=MEMORY_SUMMARY_VERSION,
            observations=observations,
            hypotheses=hypotheses,
            confidence=_aggregate_confidence(observations, hypotheses),
            source_refs=preserved_refs,
            sensitive_fields_excluded=_unique_strings(
                field for summary in monthlies for field in summary.sensitive_fields_excluded
            ),
        )

    def _daily_observations(
        self,
        *,
        feature: DerivedDailyFeature,
        assessment: ReadinessAssessment,
        recommendation: Recommendation | None,
        checkin: DailyCheckIn | None,
        confidence: float,
    ) -> list[JsonDict]:
        assessment_ref = _source_ref("readiness_assessment", assessment.id)
        feature_ref = _source_ref("derived_daily_feature", feature.id)
        observations = [
            _memory_item(
                kind="observation",
                key="readiness_assessment",
                text=(
                    "Readiness was "
                    f"{_enum_value(assessment.readiness_state)} with "
                    f"{_enum_value(assessment.recommendation_band)} guidance."
                ),
                value={
                    "readiness_state": _enum_value(assessment.readiness_state),
                    "recommendation_band": _enum_value(assessment.recommendation_band),
                    "risk_flags": list(assessment.risk_flags),
                },
                confidence=confidence,
                source_refs=[
                    {**assessment_ref, "field": "readiness_state"},
                    {**assessment_ref, "field": "recommendation_band"},
                    {**assessment_ref, "field": "risk_flags"},
                ],
            )
        ]

        if assessment.risk_flags:
            observations.append(
                _memory_item(
                    kind="observation",
                    key="risk_flags",
                    text="Risk flags present: " + ", ".join(sorted(assessment.risk_flags)) + ".",
                    value={"risk_flags": sorted(assessment.risk_flags)},
                    confidence=confidence,
                    source_refs=[{**assessment_ref, "field": "risk_flags"}],
                )
            )

        sleep_debt = _feature_value(feature.sleep_features, "sleep_debt_hours")
        if sleep_debt is not None:
            observations.append(
                _memory_item(
                    kind="observation",
                    key="sleep_debt_hours",
                    text=f"Sleep debt was {sleep_debt} h.",
                    value={"sleep_debt_hours": sleep_debt},
                    confidence=_feature_confidence(feature.sleep_features),
                    source_refs=[
                        {
                            **feature_ref,
                            "field": "sleep_features.values.sleep_debt_hours",
                        }
                    ],
                )
            )

        load_balance = _feature_value(feature.training_load_features, "load_balance")
        acute_chronic = _feature_value(feature.training_load_features, "acute_chronic_ratio")
        if load_balance is not None or acute_chronic is not None:
            observations.append(
                _memory_item(
                    kind="observation",
                    key="training_load",
                    text=_training_load_text(load_balance, acute_chronic),
                    value={
                        "load_balance": load_balance,
                        "acute_chronic_ratio": acute_chronic,
                    },
                    confidence=_feature_confidence(feature.training_load_features),
                    source_refs=[
                        {
                            **feature_ref,
                            "field": "training_load_features.values",
                        }
                    ],
                )
            )

        if checkin is not None:
            observations.append(_checkin_observation(checkin))

        if recommendation is not None:
            observations.append(_recommendation_observation(recommendation))

        return observations

    def _daily_hypotheses(
        self,
        *,
        assessment: ReadinessAssessment,
        confidence: float,
    ) -> list[JsonDict]:
        assessment_ref = _source_ref("readiness_assessment", assessment.id)
        hypotheses: list[JsonDict] = []
        for risk_flag in sorted(assessment.risk_flags):
            text = _RISK_HYPOTHESES.get(risk_flag)
            if text is None:
                continue
            hypotheses.append(
                _memory_item(
                    kind="hypothesis",
                    key=f"hypothesis_{risk_flag}",
                    text=text,
                    value={"risk_flag": risk_flag},
                    confidence=max(0.2, confidence - 0.15),
                    source_refs=[{**assessment_ref, "field": "risk_flags"}],
                )
            )
        return hypotheses

    def _weekly_observations(
        self,
        *,
        dailies: Sequence[MemorySummary],
        source_refs: list[JsonDict],
    ) -> list[JsonDict]:
        readiness_counts = Counter(
            state for summary in dailies if (state := _daily_readiness_state(summary)) is not None
        )
        risk_counts = Counter(
            risk_flag for summary in dailies for risk_flag in _daily_risk_flags(summary)
        )
        observations = [
            _memory_item(
                kind="observation",
                key="weekly_compaction_sources",
                text=f"Compiled {len(dailies)} daily memory summaries.",
                value={"daily_summary_count": len(dailies)},
                confidence=_average_confidence(dailies),
                source_refs=source_refs,
            ),
            _memory_item(
                kind="observation",
                key="weekly_readiness_arc",
                text="Weekly readiness arc: " + _counter_text(readiness_counts) + ".",
                value={"readiness_state_counts": dict(readiness_counts)},
                confidence=_average_confidence(dailies),
                source_refs=source_refs,
            ),
        ]
        if risk_counts:
            observations.append(
                _memory_item(
                    kind="observation",
                    key="weekly_notable_patterns",
                    text="Weekly risk pattern counts: " + _counter_text(risk_counts) + ".",
                    value={"risk_flag_counts": dict(risk_counts)},
                    confidence=_average_confidence(dailies),
                    source_refs=source_refs,
                )
            )
        return observations

    def _weekly_hypotheses(
        self,
        *,
        dailies: Sequence[MemorySummary],
        source_refs: list[JsonDict],
    ) -> list[JsonDict]:
        risk_counts = Counter(
            risk_flag for summary in dailies for risk_flag in _daily_risk_flags(summary)
        )
        hypotheses: list[JsonDict] = []
        for risk_flag, count in sorted(risk_counts.items()):
            if count < 2:
                continue
            text = _RISK_HYPOTHESES.get(
                risk_flag,
                f"Repeated {risk_flag} may be a notable weekly pattern.",
            )
            hypotheses.append(
                _memory_item(
                    kind="hypothesis",
                    key="repeated_weekly_pattern",
                    text=f"{text} It appeared on {count} days this week.",
                    value={"pattern": risk_flag, "days_observed": count},
                    confidence=max(0.2, _average_confidence(dailies) - 0.15),
                    source_refs=source_refs,
                )
            )
        return hypotheses

    def _monthly_observations(
        self,
        *,
        dailies: Sequence[MemorySummary],
        weeklies: Sequence[MemorySummary],
        start_date: dt.date,
        end_date: dt.date,
        source_refs: list[JsonDict],
    ) -> list[JsonDict]:
        readiness_counts = _readiness_counts(dailies=dailies, period_summaries=weeklies)
        risk_counts = _risk_counts(dailies=dailies, period_summaries=weeklies)
        expected_days = _inclusive_day_count(start_date, end_date)
        daily_count = len(dailies)
        covered_daily_count = _covered_daily_count(dailies=dailies, weeklies=weeklies)
        confidence = _average_confidence([*dailies, *weeklies])
        observations = [
            _memory_item(
                kind="observation",
                key="monthly_compaction_sources",
                text=(f"Compiled {daily_count} daily and {len(weeklies)} weekly memory summaries."),
                value={
                    "daily_summary_count": daily_count,
                    "weekly_summary_count": len(weeklies),
                    "covered_daily_summary_count": covered_daily_count,
                },
                confidence=confidence,
                source_refs=source_refs,
            ),
            _memory_item(
                kind="observation",
                key="monthly_readiness_arc",
                text="Monthly readiness arc: " + _counter_text(readiness_counts) + ".",
                value={"readiness_state_counts": dict(readiness_counts)},
                confidence=confidence,
                source_refs=source_refs,
            ),
            _memory_item(
                kind="observation",
                key="monthly_consistency",
                text=(
                    f"Monthly memory coverage: {covered_daily_count}/{expected_days} "
                    "daily summaries."
                ),
                value={
                    "daily_summary_count": covered_daily_count,
                    "expected_day_count": expected_days,
                    "daily_coverage_ratio": _ratio(covered_daily_count, expected_days),
                },
                confidence=confidence,
                source_refs=source_refs,
            ),
        ]
        if risk_counts:
            observations.append(
                _memory_item(
                    kind="observation",
                    key="monthly_risk_patterns",
                    text="Monthly risk pattern counts: " + _counter_text(risk_counts) + ".",
                    value={"risk_flag_counts": dict(risk_counts)},
                    confidence=confidence,
                    source_refs=source_refs,
                )
            )
        metric_deltas = _metric_deltas_from_dailies(dailies)
        if metric_deltas:
            observations.append(
                _memory_item(
                    kind="observation",
                    key="monthly_medium_term_deltas",
                    text="Monthly metric deltas: " + _metric_delta_text(metric_deltas) + ".",
                    value={"metrics": metric_deltas},
                    confidence=confidence,
                    source_refs=_summary_source_refs(dailies),
                )
            )
        return observations

    def _monthly_hypotheses(
        self,
        *,
        dailies: Sequence[MemorySummary],
        weeklies: Sequence[MemorySummary],
        source_refs: list[JsonDict],
    ) -> list[JsonDict]:
        risk_counts = _risk_counts(dailies=dailies, period_summaries=weeklies)
        observed_days = _covered_daily_count(dailies=dailies, weeklies=weeklies)
        if observed_days == 0:
            return []
        threshold = max(3, round(observed_days * 0.2))
        confidence = max(0.2, _average_confidence([*dailies, *weeklies]) - 0.1)
        hypotheses: list[JsonDict] = []
        for risk_flag, count in sorted(risk_counts.items()):
            if count < threshold:
                continue
            hypotheses.append(
                _memory_item(
                    kind="hypothesis",
                    key="durable_monthly_pattern",
                    text=(
                        "Learned pattern: "
                        f"{_risk_pattern_text(risk_flag)} It appeared on {count} days "
                        "this month."
                    ),
                    value={
                        "pattern": risk_flag,
                        "days_observed": count,
                        "observed_day_count": observed_days,
                    },
                    confidence=confidence,
                    source_refs=source_refs,
                )
            )
        return hypotheses

    def _quarterly_observations(
        self,
        *,
        monthlies: Sequence[MemorySummary],
        start_date: dt.date,
        end_date: dt.date,
        source_refs: list[JsonDict],
    ) -> list[JsonDict]:
        readiness_counts = _period_counts(
            monthlies,
            item_key="monthly_readiness_arc",
            value_key="readiness_state_counts",
        )
        risk_counts = _period_counts(
            monthlies,
            item_key="monthly_risk_patterns",
            value_key="risk_flag_counts",
        )
        daily_count = _period_numeric_total(
            monthlies,
            item_key="monthly_consistency",
            value_key="daily_summary_count",
        )
        expected_days = _inclusive_day_count(start_date, end_date)
        confidence = _average_confidence(monthlies)
        observations = [
            _memory_item(
                kind="observation",
                key="quarterly_compaction_sources",
                text=f"Compiled {len(monthlies)} monthly memory summaries.",
                value={"monthly_summary_count": len(monthlies)},
                confidence=confidence,
                source_refs=source_refs,
            ),
            _memory_item(
                kind="observation",
                key="quarterly_readiness_arc",
                text="Quarterly readiness arc: " + _counter_text(readiness_counts) + ".",
                value={"readiness_state_counts": dict(readiness_counts)},
                confidence=confidence,
                source_refs=source_refs,
            ),
            _memory_item(
                kind="observation",
                key="quarterly_consistency",
                text=(f"Quarterly memory coverage: {daily_count}/{expected_days} daily summaries."),
                value={
                    "daily_summary_count": daily_count,
                    "expected_day_count": expected_days,
                    "daily_coverage_ratio": _ratio(daily_count, expected_days),
                    "monthly_summary_count": len(monthlies),
                },
                confidence=confidence,
                source_refs=source_refs,
            ),
        ]
        if risk_counts:
            observations.append(
                _memory_item(
                    kind="observation",
                    key="quarterly_risk_patterns",
                    text="Quarterly risk pattern counts: " + _counter_text(risk_counts) + ".",
                    value={"risk_flag_counts": dict(risk_counts)},
                    confidence=confidence,
                    source_refs=source_refs,
                )
            )
        metric_deltas = _metric_deltas_from_monthlies(monthlies)
        if metric_deltas:
            observations.append(
                _memory_item(
                    kind="observation",
                    key="quarterly_medium_term_deltas",
                    text="Quarterly metric deltas: " + _metric_delta_text(metric_deltas) + ".",
                    value={"metrics": metric_deltas},
                    confidence=confidence,
                    source_refs=source_refs,
                )
            )
        return observations

    def _quarterly_hypotheses(
        self,
        *,
        monthlies: Sequence[MemorySummary],
        source_refs: list[JsonDict],
    ) -> list[JsonDict]:
        risk_counts = _period_counts(
            monthlies,
            item_key="monthly_risk_patterns",
            value_key="risk_flag_counts",
        )
        daily_count = _period_numeric_total(
            monthlies,
            item_key="monthly_consistency",
            value_key="daily_summary_count",
        )
        threshold = max(6, round(daily_count * 0.2))
        month_counts = _pattern_period_counts(
            monthlies,
            item_key="monthly_risk_patterns",
            value_key="risk_flag_counts",
        )
        confidence = max(0.2, _average_confidence(monthlies) - 0.1)
        hypotheses: list[JsonDict] = []
        for risk_flag, count in sorted(risk_counts.items()):
            months_observed = month_counts.get(risk_flag, 0)
            if count < threshold and months_observed < 2:
                continue
            hypotheses.append(
                _memory_item(
                    kind="hypothesis",
                    key="durable_quarterly_pattern",
                    text=(
                        "Learned durable pattern: "
                        f"{_risk_pattern_text(risk_flag)} It appeared on {count} days "
                        f"across {months_observed} months this quarter."
                    ),
                    value={
                        "pattern": risk_flag,
                        "days_observed": count,
                        "months_observed": months_observed,
                        "observed_day_count": daily_count,
                    },
                    confidence=confidence,
                    source_refs=source_refs,
                )
            )
        return hypotheses


def _checkin_observation(checkin: DailyCheckIn) -> JsonDict:
    source_ref = _source_ref("daily_check_in", checkin.id)
    flags = {
        "illness_flag": checkin.illness_flag,
        "injury_flag": checkin.injury_flag,
        "travel_flag": checkin.travel_flag,
        "alcohol_flag": checkin.alcohol_flag,
    }
    scores = {
        "energy_score": checkin.energy_score,
        "soreness_score": checkin.soreness_score,
        "stress_score": checkin.stress_score,
        "perceived_recovery_score": checkin.perceived_recovery_score,
    }
    active_flags = [key for key, value in flags.items() if value]
    return _memory_item(
        kind="observation",
        key="daily_check_in_structured_signals",
        text=_checkin_text(active_flags, scores),
        value={
            "active_flags": active_flags,
            "scores": {key: value for key, value in scores.items() if value is not None},
        },
        confidence=0.7,
        source_refs=[
            {**source_ref, "field": field}
            for field in [
                "energy_score",
                "soreness_score",
                "stress_score",
                "perceived_recovery_score",
                "illness_flag",
                "injury_flag",
                "travel_flag",
                "alcohol_flag",
            ]
        ],
    )


def _recommendation_observation(recommendation: Recommendation) -> JsonDict:
    source_ref = _source_ref("recommendation", recommendation.id)
    return _memory_item(
        kind="observation",
        key="daily_outcome",
        text=(
            "Daily recommendation was persisted with "
            f"{_enum_value(recommendation.safety_status)} safety status."
        ),
        value={
            "recommendation_type": _enum_value(recommendation.recommendation_type),
            "safety_status": _enum_value(recommendation.safety_status),
            "accepted_action": recommendation.accepted_action,
            "feedback_present": recommendation.user_feedback is not None,
        },
        confidence=0.7,
        source_refs=[
            {**source_ref, "field": "recommendation_type"},
            {**source_ref, "field": "safety_status"},
            {**source_ref, "field": "accepted_action"},
            {**source_ref, "field": "user_feedback"},
        ],
    )


def _memory_item(
    *,
    kind: str,
    key: str,
    text: str,
    value: Any,
    confidence: float,
    source_refs: Sequence[Mapping[str, Any]],
) -> JsonDict:
    return {
        "kind": kind,
        "key": key,
        "text": text,
        "value": _jsonable(value),
        "confidence": _bounded_confidence(confidence),
        "source_refs": _unique_refs(source_refs),
    }


def _feature_value(section: Mapping[str, Any], key: str) -> Any | None:
    values = section.get("values")
    if not isinstance(values, Mapping):
        return None
    raw = values.get(key)
    if isinstance(raw, Mapping) and raw.get("status") == "computed":
        return raw.get("value")
    return raw


def _feature_confidence(section: Mapping[str, Any]) -> float:
    quality = section.get("data_quality")
    if not isinstance(quality, Mapping):
        return 0.7
    completeness = quality.get("completeness")
    if isinstance(completeness, int | float) and not isinstance(completeness, bool):
        return _bounded_confidence(float(completeness))
    return 0.7


def _training_load_text(load_balance: Any | None, acute_chronic: Any | None) -> str:
    parts: list[str] = []
    if load_balance is not None:
        parts.append(f"load balance was {load_balance}")
    if acute_chronic is not None:
        parts.append(f"acute:chronic ratio was {acute_chronic}")
    return "Training load " + " and ".join(parts) + "."


def _checkin_text(active_flags: Sequence[str], scores: Mapping[str, Any]) -> str:
    score_text = ", ".join(f"{key}={value}" for key, value in scores.items() if value is not None)
    if active_flags:
        return "Check-in structured flags: " + ", ".join(active_flags) + f"; scores: {score_text}."
    return f"Check-in structured scores: {score_text}."


def _daily_readiness_state(summary: MemorySummary) -> str | None:
    for item in summary.observations:
        if item.get("key") != "readiness_assessment":
            continue
        value = item.get("value")
        if isinstance(value, Mapping):
            raw = value.get("readiness_state")
            return str(raw) if raw is not None else None
    return None


def _daily_risk_flags(summary: MemorySummary) -> list[str]:
    flags: list[str] = []
    for item in summary.observations:
        value = item.get("value")
        if not isinstance(value, Mapping):
            continue
        raw = value.get("risk_flags")
        if isinstance(raw, list):
            flags.extend(str(flag) for flag in raw)
    return _unique_strings(flags)


def _validate_compaction_inputs(
    summaries: Sequence[MemorySummary],
    *,
    user_id: UUID,
    period_type: PeriodType,
    start_date: dt.date,
    end_date: dt.date,
    label: str,
) -> None:
    for summary in summaries:
        if summary.user_id != user_id:
            raise ValueError(f"{label} summary must belong to user_id")
        if summary.period_type != period_type:
            raise ValueError(f"compiler only accepts {label} summaries")
        if summary.start_date < start_date or summary.end_date > end_date:
            raise ValueError(f"{label} summary is outside compaction period")


def _validate_contiguous_coverage(
    summaries: Sequence[MemorySummary],
    *,
    start_date: dt.date,
    end_date: dt.date,
    label: str,
) -> None:
    if not summaries:
        raise ValueError(f"{label} summaries must cover requested period")

    ordered = sorted(summaries, key=lambda summary: summary.start_date)
    if ordered[0].start_date != start_date or ordered[-1].end_date != end_date:
        raise ValueError(f"{label} summaries must cover requested period")

    previous_end = ordered[0].end_date
    for summary in ordered[1:]:
        if summary.start_date != previous_end + dt.timedelta(days=1):
            raise ValueError(f"{label} summaries must cover requested period without gaps")
        previous_end = summary.end_date


def _reconcile_monthly_inputs(
    *,
    dailies: Sequence[MemorySummary],
    weeklies: Sequence[MemorySummary],
) -> tuple[list[MemorySummary], list[MemorySummary]]:
    daily_dates = {summary.start_date for summary in dailies}
    reconciled_weeklies: list[MemorySummary] = []
    for weekly in weeklies:
        weekly_dates = _weekly_coverage_dates(weekly)
        overlap = daily_dates & weekly_dates
        if not overlap:
            reconciled_weeklies.append(weekly)
            continue
        if weekly_dates <= daily_dates:
            continue
        raise ValueError("weekly summaries must not partially overlap daily summaries")
    return list(dailies), reconciled_weeklies


def _weekly_coverage_dates(summary: MemorySummary) -> set[dt.date]:
    covered_count = _weekly_daily_count(summary)
    ref_dates = _daily_memory_ref_dates(summary)
    if ref_dates:
        if len(ref_dates) != covered_count:
            raise ValueError("weekly source refs must reconcile with daily compaction count")
        return ref_dates

    range_dates = _date_set(summary.start_date, summary.end_date)
    if len(range_dates) != covered_count:
        raise ValueError("weekly summaries must include daily source refs for partial coverage")
    return range_dates


def _daily_memory_ref_dates(summary: MemorySummary) -> set[dt.date]:
    dates: set[dt.date] = set()
    for ref in summary.source_refs:
        if not isinstance(ref, Mapping):
            continue
        if ref.get("table") != "memory_summary" or ref.get("period_type") != PeriodType.daily.value:
            continue
        start_date = _date_ref(ref.get("start_date"))
        end_date = _date_ref(ref.get("end_date"))
        if start_date is None or end_date is None or start_date != end_date:
            continue
        dates.add(start_date)
    return dates


def _validate_calendar_quarter_period(
    *,
    start_date: dt.date,
    end_date: dt.date,
) -> None:
    if start_date.day != 1 or start_date.month not in {1, 4, 7, 10}:
        raise ValueError("quarterly summaries must use calendar quarter boundaries")
    if end_date != _month_end(_add_months(start_date, 2)):
        raise ValueError("quarterly summaries must use calendar quarter boundaries")


def _validate_calendar_monthly_inputs(
    summaries: Sequence[MemorySummary],
    *,
    start_date: dt.date,
    end_date: dt.date,
) -> None:
    expected_bounds = _calendar_month_bounds(start_date=start_date, end_date=end_date)
    actual_bounds = [(summary.start_date, summary.end_date) for summary in summaries]
    if actual_bounds != expected_bounds:
        raise ValueError("monthly summaries must match calendar months in requested quarter")


def _covered_daily_count(
    *,
    dailies: Sequence[MemorySummary],
    weeklies: Sequence[MemorySummary],
) -> int:
    return len(dailies) + sum(_weekly_daily_count(weekly) for weekly in weeklies)


def _weekly_daily_count(summary: MemorySummary) -> int:
    for item in summary.observations:
        if item.get("key") != "weekly_compaction_sources":
            continue
        value = item.get("value")
        if not isinstance(value, Mapping):
            break
        raw_count = value.get("daily_summary_count")
        if isinstance(raw_count, int | float) and not isinstance(raw_count, bool):
            count = int(raw_count)
            if count > 0:
                return count
            break
    raise ValueError("weekly summaries must include daily compaction source counts")


def _readiness_counts(
    *,
    dailies: Sequence[MemorySummary],
    period_summaries: Sequence[MemorySummary],
) -> Counter[str]:
    counts = Counter(
        state for summary in dailies if (state := _daily_readiness_state(summary)) is not None
    )
    counts.update(
        _period_counts(
            period_summaries,
            item_key="weekly_readiness_arc",
            value_key="readiness_state_counts",
        )
    )
    return counts


def _risk_counts(
    *,
    dailies: Sequence[MemorySummary],
    period_summaries: Sequence[MemorySummary],
) -> Counter[str]:
    counts = Counter(risk_flag for summary in dailies for risk_flag in _daily_risk_flags(summary))
    counts.update(
        _period_counts(
            period_summaries,
            item_key="weekly_notable_patterns",
            value_key="risk_flag_counts",
        )
    )
    return counts


def _period_counts(
    summaries: Sequence[MemorySummary],
    *,
    item_key: str,
    value_key: str,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for summary in summaries:
        for item in summary.observations:
            if item.get("key") != item_key:
                continue
            value = item.get("value")
            if not isinstance(value, Mapping):
                continue
            raw_counts = value.get(value_key)
            if not isinstance(raw_counts, Mapping):
                continue
            for raw_key, raw_count in raw_counts.items():
                if isinstance(raw_count, int | float) and not isinstance(raw_count, bool):
                    counts[str(raw_key)] += int(raw_count)
    return counts


def _period_numeric_total(
    summaries: Sequence[MemorySummary],
    *,
    item_key: str,
    value_key: str,
) -> int:
    total = 0
    for summary in summaries:
        for item in summary.observations:
            if item.get("key") != item_key:
                continue
            value = item.get("value")
            if not isinstance(value, Mapping):
                continue
            raw = value.get(value_key)
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                total += int(raw)
            break
    return total


def _pattern_period_counts(
    summaries: Sequence[MemorySummary],
    *,
    item_key: str,
    value_key: str,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for summary in summaries:
        period_counts = _period_counts([summary], item_key=item_key, value_key=value_key)
        for pattern, count in period_counts.items():
            if count > 0:
                counts[pattern] += 1
    return counts


def _metric_deltas_from_dailies(dailies: Sequence[MemorySummary]) -> JsonDict:
    metrics: JsonDict = {}
    ordered = sorted(dailies, key=lambda summary: summary.start_date)
    for metric_key in _TREND_METRIC_KEYS:
        values = [
            value
            for summary in ordered
            if (value := _daily_metric_value(summary, metric_key)) is not None
        ]
        if not values:
            continue
        first_value = values[0]
        last_value = values[-1]
        metrics[metric_key] = {
            "first_value": _rounded_metric(first_value),
            "last_value": _rounded_metric(last_value),
            "delta": _rounded_metric(last_value - first_value),
            "sample_count": len(values),
        }
    return metrics


def _metric_deltas_from_monthlies(monthlies: Sequence[MemorySummary]) -> JsonDict:
    metrics: JsonDict = {}
    for summary in sorted(monthlies, key=lambda item: item.start_date):
        monthly_metrics = _summary_metric_deltas(summary)
        for metric_key, delta in monthly_metrics.items():
            if not isinstance(delta, Mapping):
                continue
            first_value = _numeric_value(delta.get("first_value"))
            last_value = _numeric_value(delta.get("last_value"))
            sample_count = _numeric_value(delta.get("sample_count"))
            if first_value is None or last_value is None:
                continue
            if metric_key not in metrics:
                metrics[metric_key] = {
                    "first_value": _rounded_metric(first_value),
                    "last_value": _rounded_metric(last_value),
                    "delta": _rounded_metric(last_value - first_value),
                    "sample_count": int(sample_count or 0),
                }
                continue
            metrics[metric_key]["last_value"] = _rounded_metric(last_value)
            metrics[metric_key]["delta"] = _rounded_metric(
                metrics[metric_key]["last_value"] - metrics[metric_key]["first_value"]
            )
            metrics[metric_key]["sample_count"] += int(sample_count or 0)
    return metrics


def _summary_metric_deltas(summary: MemorySummary) -> Mapping[str, Any]:
    for item in summary.observations:
        if item.get("key") != "monthly_medium_term_deltas":
            continue
        value = item.get("value")
        if not isinstance(value, Mapping):
            return {}
        metrics = value.get("metrics")
        return metrics if isinstance(metrics, Mapping) else {}
    return {}


def _daily_metric_value(summary: MemorySummary, metric_key: str) -> float | None:
    for item in summary.observations:
        if item.get("key") != metric_key:
            continue
        raw_value = item.get("value")
        if isinstance(raw_value, Mapping):
            return _numeric_value(raw_value.get(metric_key))
        return _numeric_value(raw_value)
    return None


def _numeric_value(raw: Any) -> float | None:
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    return None


def _metric_delta_text(metrics: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for metric_key, raw_delta in sorted(metrics.items()):
        if not isinstance(raw_delta, Mapping):
            continue
        parts.append(
            f"{metric_key} {raw_delta.get('first_value')}->{raw_delta.get('last_value')} "
            f"(delta {raw_delta.get('delta')})"
        )
    return ", ".join(parts) if parts else "none observed"


def _risk_pattern_text(risk_flag: str) -> str:
    return _RISK_HYPOTHESES.get(
        risk_flag,
        f"Repeated {risk_flag} may be a durable personal pattern.",
    )


def _inclusive_day_count(start_date: dt.date, end_date: dt.date) -> int:
    return (end_date - start_date).days + 1


def _date_set(start_date: dt.date, end_date: dt.date) -> set[dt.date]:
    return {
        start_date + dt.timedelta(days=offset)
        for offset in range(_inclusive_day_count(start_date, end_date))
    }


def _date_ref(raw: Any) -> dt.date | None:
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    if isinstance(raw, str):
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _calendar_month_bounds(
    *,
    start_date: dt.date,
    end_date: dt.date,
) -> list[tuple[dt.date, dt.date]]:
    bounds: list[tuple[dt.date, dt.date]] = []
    current = start_date
    while current <= end_date:
        month_end = _month_end(current)
        bounds.append((current, month_end))
        current = month_end + dt.timedelta(days=1)
    return bounds


def _add_months(value: dt.date, months: int) -> dt.date:
    month_index = value.month - 1 + months
    year = value.year + (month_index // 12)
    month = (month_index % 12) + 1
    return dt.date(year, month, min(value.day, _month_end(dt.date(year, month, 1)).day))


def _month_end(value: dt.date) -> dt.date:
    if value.month == 12:
        return dt.date(value.year, 12, 31)
    return dt.date(value.year, value.month + 1, 1) - dt.timedelta(days=1)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _bounded_confidence(numerator / denominator)


def _rounded_metric(value: float) -> float:
    return round(value, 3)


def _counter_text(counter: Mapping[str, int]) -> str:
    if not counter:
        return "none observed"
    return ", ".join(f"{key}={value}" for key, value in sorted(counter.items()))


def _confidence_score(raw: Any) -> float:
    return _CONFIDENCE_SCORES.get(raw, 0.7)


def _aggregate_confidence(
    observations: Sequence[JsonDict],
    hypotheses: Sequence[JsonDict],
) -> float:
    values = [
        float(item["confidence"])
        for item in [*observations, *hypotheses]
        if isinstance(item.get("confidence"), int | float)
    ]
    if not values:
        return 0.0
    return _bounded_confidence(sum(values) / len(values))


def _average_confidence(summaries: Sequence[MemorySummary]) -> float:
    if not summaries:
        return 0.0
    return _bounded_confidence(sum(summary.confidence for summary in summaries) / len(summaries))


def _bounded_confidence(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def _source_ref(table: str, row_id: UUID) -> JsonDict:
    return {"table": table, "id": str(row_id)}


def _memory_summary_ref(summary: MemorySummary) -> JsonDict:
    return {
        "table": "memory_summary",
        "id": str(summary.id),
        "period_type": summary.period_type.value,
        "start_date": summary.start_date.isoformat(),
        "end_date": summary.end_date.isoformat(),
    }


def _summary_source_refs(summaries: Sequence[MemorySummary]) -> list[JsonDict]:
    summary_refs = [_memory_summary_ref(summary) for summary in summaries]
    source_refs = [ref for row in summaries for ref in row.source_refs]
    return _unique_refs([*summary_refs, *source_refs])


def _source_sample_refs(feature: DerivedDailyFeature) -> list[JsonDict]:
    return [
        {
            "table": "source_sample",
            "source_id": source_id,
            "derived_daily_feature_id": str(feature.id),
        }
        for source_id in feature.source_sample_ids
    ]


def _flatten_item_refs(items: Sequence[Mapping[str, Any]]) -> list[JsonDict]:
    refs: list[JsonDict] = []
    for item in items:
        raw_refs = item.get("source_refs")
        if isinstance(raw_refs, list):
            refs.extend(ref for ref in raw_refs if isinstance(ref, dict))
    return refs


def _unique_refs(refs: Sequence[Mapping[str, Any]]) -> list[JsonDict]:
    seen: set[str] = set()
    result: list[JsonDict] = []
    for ref in refs:
        item = {str(key): _jsonable(value) for key, value in ref.items()}
        marker = json.dumps(item, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def _unique_strings(items: Sequence[str] | Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(str(item))
        result.append(str(item))
    return result


def _enum_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(raw) for key, raw in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dt.date | dt.datetime | UUID):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    return value
