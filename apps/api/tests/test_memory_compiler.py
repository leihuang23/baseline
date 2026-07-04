"""P4-01 memory compiler tests."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, select

from baseline_api.db.models import (
    AuditEvent,
    DailyCheckIn,
    DerivedDailyFeature,
    MemorySummary,
    ReadinessAssessment,
    Recommendation,
    User,
)
from baseline_api.db.models.enums import (
    AuditEventType,
    ConfidenceLevel,
    PeriodType,
    PrivacyMode,
    ReadinessState,
    RecommendationBand,
    RecommendationType,
    RedactionStatus,
    SafetyStatus,
    SensitiveNotePolicy,
)
from baseline_api.memory.compiler import MemoryCompiler
from baseline_api.memory.service import MemoryService, _validated_items


def _value(value: Any, unit: str = "unit") -> dict[str, Any]:
    return {"status": "computed", "value": value, "unit": unit}


def _user(db_session: Session) -> User:
    user = User(privacy_mode=PrivacyMode.local_only, active_consent_version="v1")
    db_session.add(user)
    db_session.flush()
    return user


def _feature_row(user_id: UUID, target_date: dt.date) -> DerivedDailyFeature:
    return DerivedDailyFeature(
        user_id=user_id,
        date=target_date,
        feature_version="test-v1",
        sleep_features={
            "values": {"sleep_debt_hours": _value(2.4, "h")},
            "data_quality": {"flags": [], "completeness": 0.95},
        },
        hrv_features={
            "values": {"deviation_pct": _value(-6.0, "percent")},
            "data_quality": {"flags": [], "completeness": 0.95},
        },
        rhr_features={
            "values": {
                "deviation_pct": _value(4.0, "percent"),
                "deviation_bpm": _value(3.0, "bpm"),
            },
            "data_quality": {"flags": [], "completeness": 0.95},
        },
        training_load_features={
            "values": {
                "acute_chronic_ratio": _value(1.35, "ratio"),
                "load_balance": _value("high_spike", "category"),
                "density_by_modality": _value(
                    {"run": {"status": "computed", "value": 3, "unit": "sessions"}},
                    "structured",
                ),
            },
            "data_quality": {"flags": [], "completeness": 0.9},
        },
        recovery_features={
            "values": {"level": _value("low", "category")},
            "data_quality": {"flags": [], "completeness": 0.9},
        },
        goal_features={
            "values": {},
            "data_quality": {"flags": [], "completeness": 1.0},
        },
        data_quality={
            "flags": [],
            "overall_completeness": 0.93,
            "section_completeness": {},
        },
        anomaly_flags=[],
        computed_at=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
        source_sample_ids=["sample-sleep", "sample-workout"],
    )


def _assessment_row(user_id: UUID, target_date: dt.date) -> ReadinessAssessment:
    return ReadinessAssessment(
        user_id=user_id,
        date=target_date,
        assessment_version="p3-02-v1",
        readiness_state=ReadinessState.low,
        recommendation_band=RecommendationBand.easy_or_recovery,
        confidence=ConfidenceLevel.medium,
        uncertainty=["Sleep debt and training density reduce confidence."],
        evidence_items=[
            {
                "metric": "sleep_debt_hours",
                "value": 2.4,
                "interpretation": "unfavorable",
                "source": "sleep_features.values.sleep_debt_hours",
            },
            {
                "metric": "acute_chronic_ratio",
                "value": 1.35,
                "interpretation": "unfavorable",
                "source": "training_load_features.values.acute_chronic_ratio",
            },
        ],
        risk_flags=["high_sleep_debt", "high_training_density"],
        candidate_options=[],
        follow_up_questions=[],
        goal_tradeoffs=[],
        hard_safety_flags=[],
        reasoning_trace_id=uuid4(),
    )


def _recommendation_row(user_id: UUID, target_date: dt.date) -> Recommendation:
    return Recommendation(
        user_id=user_id,
        date=target_date,
        recommendation_type=RecommendationType.recovery,
        recommendation_text="Keep today easy.",
        candidate_options=[],
        evidence_refs=[{"metric": "sleep_debt_hours"}],
        safety_status=SafetyStatus.passed,
        safety_result={"status": "passed"},
        accepted_action={"label": "easy mobility"},
        user_feedback={"rating": 4},
    )


def _checkin_row(user_id: UUID, target_date: dt.date) -> DailyCheckIn:
    return DailyCheckIn(
        user_id=user_id,
        date=target_date,
        energy_score=4,
        mood_score=5,
        soreness_score=7,
        stress_score=6,
        perceived_recovery_score=3,
        alcohol_flag=False,
        caffeine_notes="SENSITIVE_CAFFEINE_TOKEN",
        illness_flag=False,
        injury_flag=False,
        travel_flag=True,
        sensitive_note_policy=SensitiveNotePolicy.exclude_from_external_llm,
        redaction_status=RedactionStatus.redacted,
        structured_notes={"opaque_context": "SENSITIVE_STRUCTURED_TOKEN"},
        free_text_note_reference="SENSITIVE_REFERENCE_TOKEN",
        free_text_note_summary="SENSITIVE_SUMMARY_TOKEN",
    )


def _daily_memory_summary(
    user_id: UUID,
    target_date: dt.date,
    *,
    risk_flags: list[str],
    readiness_state: str,
    offset: int,
    vo2_max: float | None = None,
    sensitive_text: str | None = None,
    sensitive_fields_excluded: list[str] | None = None,
) -> MemorySummary:
    observations = [
        {
            "kind": "observation",
            "key": "readiness_assessment",
            "text": f"Readiness was {readiness_state}.",
            "value": {
                "readiness_state": readiness_state,
                "risk_flags": risk_flags,
            },
            "confidence": 0.82,
            "source_refs": [
                {
                    "table": "readiness_assessment",
                    "id": f"assessment-{offset}",
                    "field": "risk_flags",
                }
            ],
        }
    ]
    if vo2_max is not None:
        observations.append(
            {
                "kind": "observation",
                "key": "vo2_max",
                "text": f"VO2 max estimate was {vo2_max}.",
                "value": {"vo2_max": vo2_max},
                "confidence": 0.78,
                "source_refs": [
                    {
                        "table": "derived_daily_feature",
                        "id": f"feature-{offset}",
                        "field": "cardio_features.values.vo2_max",
                    }
                ],
            }
        )
    if sensitive_text is not None:
        observations.append(
            {
                "kind": "observation",
                "key": "local_sensitive_note_placeholder",
                "text": sensitive_text,
                "value": {"note": sensitive_text},
                "confidence": 0.2,
                "source_refs": [{"table": "daily_check_in", "id": f"checkin-{offset}"}],
            }
        )
    return MemorySummary(
        user_id=user_id,
        period_type=PeriodType.daily,
        start_date=target_date,
        end_date=target_date,
        summary_version="memory-summary-v1",
        observations=observations,
        hypotheses=[],
        confidence=0.8,
        source_refs=[
            {
                "table": "derived_daily_feature",
                "id": f"feature-{offset}",
                "field": "source_sample_ids",
            }
        ],
        sensitive_fields_excluded=sensitive_fields_excluded or [],
    )


def test_daily_summary_separates_observations_hypotheses_and_source_refs() -> None:
    user_id = uuid4()
    target_date = dt.date(2026, 7, 4)
    feature = _feature_row(user_id, target_date)
    assessment = _assessment_row(user_id, target_date)
    recommendation = _recommendation_row(user_id, target_date)
    checkin = _checkin_row(user_id, target_date)

    summary = MemoryCompiler().compile_daily(
        user_id=user_id,
        feature=feature,
        assessment=assessment,
        recommendation=recommendation,
        checkin=checkin,
    )

    assert summary.period_type == PeriodType.daily
    assert summary.start_date == target_date
    assert summary.end_date == target_date
    assert summary.observations
    assert summary.hypotheses
    assert {item["kind"] for item in summary.observations} == {"observation"}
    assert {item["kind"] for item in summary.hypotheses} == {"hypothesis"}
    for item in [*summary.observations, *summary.hypotheses]:
        assert 0 < item["confidence"] <= 1
        assert item["source_refs"]

    source_tables = {ref["table"] for ref in summary.source_refs}
    assert {"derived_daily_feature", "readiness_assessment", "daily_check_in"} <= source_tables
    assert any(ref["table"] == "recommendation" for ref in summary.source_refs)
    assert any(
        ref["table"] == "source_sample" and ref["source_id"] == "sample-workout"
        for ref in summary.source_refs
    )


def test_sensitive_note_fields_are_excluded_from_daily_memory_by_default() -> None:
    user_id = uuid4()
    target_date = dt.date(2026, 7, 4)
    feature = _feature_row(user_id, target_date)
    assessment = _assessment_row(user_id, target_date)
    checkin = _checkin_row(user_id, target_date)

    summary = MemoryCompiler().compile_daily(
        user_id=user_id,
        feature=feature,
        assessment=assessment,
        checkin=checkin,
    )
    serialized = json.dumps(summary.model_dump(mode="json"), sort_keys=True)

    assert "SENSITIVE_CAFFEINE_TOKEN" not in serialized
    assert "SENSITIVE_STRUCTURED_TOKEN" not in serialized
    assert "SENSITIVE_REFERENCE_TOKEN" not in serialized
    assert "SENSITIVE_SUMMARY_TOKEN" not in serialized
    assert "daily_check_in.caffeine_notes" in summary.sensitive_fields_excluded
    assert "daily_check_in.structured_notes" in summary.sensitive_fields_excluded
    assert "daily_check_in.free_text_note_reference" in summary.sensitive_fields_excluded
    assert "daily_check_in.free_text_note_summary" in summary.sensitive_fields_excluded


def test_memory_items_require_structured_source_refs() -> None:
    with pytest.raises(ValueError, match="structured source_refs"):
        _validated_items(
            [
                {
                    "kind": "observation",
                    "key": "invalid_ref",
                    "text": "Invalid source ref.",
                    "confidence": 0.7,
                    "source_refs": ["not-a-ref"],
                }
            ],
            kind="observation",
        )


def test_weekly_summary_compacts_daily_records_and_preserves_refs() -> None:
    user_id = uuid4()
    start = dt.date(2026, 6, 28)
    dailies: list[MemorySummary] = []
    for offset in range(7):
        day = start + dt.timedelta(days=offset)
        risk_flags = ["high_sleep_debt"] if offset in {1, 3, 5} else []
        summary = MemorySummary(
            user_id=user_id,
            period_type=PeriodType.daily,
            start_date=day,
            end_date=day,
            summary_version="memory-summary-v1",
            observations=[
                {
                    "kind": "observation",
                    "key": "readiness_assessment",
                    "text": f"Readiness was {'low' if risk_flags else 'high'}.",
                    "value": {
                        "readiness_state": "low" if risk_flags else "high",
                        "risk_flags": risk_flags,
                    },
                    "confidence": 0.8,
                    "source_refs": [
                        {
                            "table": "readiness_assessment",
                            "id": f"assessment-{offset}",
                            "field": "risk_flags",
                        }
                    ],
                }
            ],
            hypotheses=[],
            confidence=0.8,
            source_refs=[
                {
                    "table": "derived_daily_feature",
                    "id": f"feature-{offset}",
                    "field": "source_sample_ids",
                }
            ],
            sensitive_fields_excluded=[],
        )
        dailies.append(summary)

    weekly = MemoryCompiler().compile_weekly(
        user_id=user_id,
        start_date=start,
        end_date=start + dt.timedelta(days=6),
        daily_summaries=dailies,
    )

    assert weekly.period_type == PeriodType.weekly
    assert any(item["key"] == "weekly_readiness_arc" for item in weekly.observations)
    assert any(
        item["key"] == "repeated_weekly_pattern" and item["value"]["pattern"] == "high_sleep_debt"
        for item in weekly.hypotheses
    )
    assert any(
        ref["table"] == "memory_summary" and ref["id"] == str(dailies[0].id)
        for ref in weekly.source_refs
    )
    assert any(ref["table"] == "derived_daily_feature" for ref in weekly.source_refs)
    for item in [*weekly.observations, *weekly.hypotheses]:
        assert item["source_refs"]


def test_monthly_and_quarterly_summaries_reconcile_90_day_longitudinal_records() -> None:
    user_id = uuid4()
    quarter_start = dt.date(2026, 1, 1)
    dailies = [
        _daily_memory_summary(
            user_id,
            quarter_start + dt.timedelta(days=offset),
            risk_flags=["high_sleep_debt"] if offset % 3 == 0 else [],
            readiness_state="low" if offset % 3 == 0 else "high",
            offset=offset,
            vo2_max=42.0 + (offset * 0.05),
        )
        for offset in range(90)
    ]

    compiler = MemoryCompiler()
    month_bounds = [
        (dt.date(2026, 1, 1), dt.date(2026, 1, 31)),
        (dt.date(2026, 2, 1), dt.date(2026, 2, 28)),
        (dt.date(2026, 3, 1), dt.date(2026, 3, 31)),
    ]
    monthlies = [
        compiler.compile_monthly(
            user_id=user_id,
            start_date=start,
            end_date=end,
            daily_summaries=[summary for summary in dailies if start <= summary.start_date <= end],
            weekly_summaries=[],
        )
        for start, end in month_bounds
    ]

    quarterly = compiler.compile_quarterly(
        user_id=user_id,
        start_date=quarter_start,
        end_date=dt.date(2026, 3, 31),
        monthly_summaries=monthlies,
    )

    readiness = next(
        item for item in quarterly.observations if item["key"] == "quarterly_readiness_arc"
    )
    risk_patterns = next(
        item for item in quarterly.observations if item["key"] == "quarterly_risk_patterns"
    )
    consistency = next(
        item for item in quarterly.observations if item["key"] == "quarterly_consistency"
    )
    deltas = next(
        item for item in quarterly.observations if item["key"] == "quarterly_medium_term_deltas"
    )

    assert readiness["value"]["readiness_state_counts"] == {"high": 60, "low": 30}
    assert risk_patterns["value"]["risk_flag_counts"] == {"high_sleep_debt": 30}
    assert consistency["value"]["daily_summary_count"] == 90
    assert consistency["value"]["expected_day_count"] == 90
    assert deltas["value"]["metrics"]["vo2_max"]["first_value"] == 42.0
    assert deltas["value"]["metrics"]["vo2_max"]["last_value"] == 46.45
    assert any(
        item["key"] == "durable_quarterly_pattern"
        and item["value"]["pattern"] == "high_sleep_debt"
        and item["value"]["days_observed"] == 30
        for item in quarterly.hypotheses
    )
    assert all(item["source_refs"] for item in [*quarterly.observations, *quarterly.hypotheses])
    assert any(ref["table"] == "memory_summary" for ref in quarterly.source_refs)
    assert any(ref["table"] == "derived_daily_feature" for ref in quarterly.source_refs)


def test_monthly_weekly_fallback_uses_weekly_daily_coverage() -> None:
    user_id = uuid4()
    start = dt.date(2026, 1, 1)
    dailies = [
        _daily_memory_summary(
            user_id,
            start + dt.timedelta(days=offset),
            risk_flags=["high_sleep_debt", "high_training_density"],
            readiness_state="low",
            offset=offset,
        )
        for offset in range(7)
    ]

    compiler = MemoryCompiler()
    weekly = compiler.compile_weekly(
        user_id=user_id,
        start_date=start,
        end_date=start + dt.timedelta(days=6),
        daily_summaries=dailies,
    )
    monthly = compiler.compile_monthly(
        user_id=user_id,
        start_date=start,
        end_date=dt.date(2026, 1, 31),
        daily_summaries=[],
        weekly_summaries=[weekly],
    )

    consistency = next(
        item for item in monthly.observations if item["key"] == "monthly_consistency"
    )
    risk_patterns = next(
        item for item in monthly.observations if item["key"] == "monthly_risk_patterns"
    )

    assert consistency["value"]["daily_summary_count"] == 7
    assert consistency["value"]["expected_day_count"] == 31
    assert consistency["value"]["daily_coverage_ratio"] == 0.226
    assert risk_patterns["value"]["risk_flag_counts"] == {
        "high_sleep_debt": 7,
        "high_training_density": 7,
    }
    assert {
        item["value"]["pattern"]: item["value"]["observed_day_count"] for item in monthly.hypotheses
    } == {
        "high_sleep_debt": 7,
        "high_training_density": 7,
    }


def test_monthly_mixed_daily_and_weekly_inputs_reconcile_counts_and_refs() -> None:
    user_id = uuid4()
    start = dt.date(2026, 1, 1)
    explicit_dailies = [
        _daily_memory_summary(
            user_id,
            start + dt.timedelta(days=offset),
            risk_flags=["high_sleep_debt"],
            readiness_state="low",
            offset=offset,
        )
        for offset in range(3)
    ]
    weekly_dailies = [
        _daily_memory_summary(
            user_id,
            start + dt.timedelta(days=offset),
            risk_flags=["high_training_density"],
            readiness_state="high",
            offset=offset,
        )
        for offset in range(3, 10)
    ]

    compiler = MemoryCompiler()
    weekly = compiler.compile_weekly(
        user_id=user_id,
        start_date=start + dt.timedelta(days=3),
        end_date=start + dt.timedelta(days=9),
        daily_summaries=weekly_dailies,
    )
    monthly = compiler.compile_monthly(
        user_id=user_id,
        start_date=start,
        end_date=dt.date(2026, 1, 31),
        daily_summaries=explicit_dailies,
        weekly_summaries=[weekly],
    )

    compaction_sources = next(
        item for item in monthly.observations if item["key"] == "monthly_compaction_sources"
    )
    readiness = next(
        item for item in monthly.observations if item["key"] == "monthly_readiness_arc"
    )
    risk_patterns = next(
        item for item in monthly.observations if item["key"] == "monthly_risk_patterns"
    )

    assert compaction_sources["value"] == {
        "daily_summary_count": 3,
        "weekly_summary_count": 1,
        "covered_daily_summary_count": 10,
    }
    assert readiness["value"]["readiness_state_counts"] == {"high": 7, "low": 3}
    assert risk_patterns["value"]["risk_flag_counts"] == {
        "high_sleep_debt": 3,
        "high_training_density": 7,
    }
    assert any(
        ref["table"] == "memory_summary" and ref["id"] == str(weekly.id)
        for ref in monthly.source_refs
    )


def test_monthly_compaction_ignores_weeklies_already_represented_by_daily_inputs() -> None:
    user_id = uuid4()
    start = dt.date(2026, 1, 1)
    dailies = [
        _daily_memory_summary(
            user_id,
            start + dt.timedelta(days=offset),
            risk_flags=["high_sleep_debt"],
            readiness_state="low",
            offset=offset,
        )
        for offset in range(7)
    ]

    compiler = MemoryCompiler()
    weekly = compiler.compile_weekly(
        user_id=user_id,
        start_date=start,
        end_date=start + dt.timedelta(days=6),
        daily_summaries=dailies,
    )
    monthly = compiler.compile_monthly(
        user_id=user_id,
        start_date=start,
        end_date=dt.date(2026, 1, 31),
        daily_summaries=dailies,
        weekly_summaries=[weekly],
    )

    consistency = next(
        item for item in monthly.observations if item["key"] == "monthly_consistency"
    )
    risk_patterns = next(
        item for item in monthly.observations if item["key"] == "monthly_risk_patterns"
    )

    assert consistency["value"]["daily_summary_count"] == 7
    assert risk_patterns["value"]["risk_flag_counts"] == {"high_sleep_debt": 7}
    assert not any(
        ref["table"] == "memory_summary" and ref["id"] == str(weekly.id)
        for ref in monthly.source_refs
    )


def test_monthly_compaction_rejects_partially_overlapping_daily_weekly_inputs() -> None:
    user_id = uuid4()
    start = dt.date(2026, 1, 1)
    dailies = [
        _daily_memory_summary(
            user_id,
            start + dt.timedelta(days=offset),
            risk_flags=["high_sleep_debt"],
            readiness_state="low",
            offset=offset,
        )
        for offset in range(7)
    ]
    weekly = MemoryCompiler().compile_weekly(
        user_id=user_id,
        start_date=start,
        end_date=start + dt.timedelta(days=6),
        daily_summaries=dailies,
    )

    with pytest.raises(ValueError, match="partially overlap"):
        MemoryCompiler().compile_monthly(
            user_id=user_id,
            start_date=start,
            end_date=dt.date(2026, 1, 31),
            daily_summaries=dailies[:3],
            weekly_summaries=[weekly],
        )


def test_quarterly_summary_requires_monthlies_covering_requested_quarter(
    db_session: Session,
) -> None:
    user = _user(db_session)
    service = MemoryService(db_session)
    start = dt.date(2026, 1, 1)
    for offset in range(31):
        db_session.add(
            _daily_memory_summary(
                user.id,
                start + dt.timedelta(days=offset),
                risk_flags=["high_sleep_debt"],
                readiness_state="low",
                offset=offset,
            )
        )
    db_session.commit()

    service.generate_monthly_summary(
        user_id=user.id,
        start_date=start,
        end_date=dt.date(2026, 1, 31),
    )

    with pytest.raises(ValueError, match="monthly summaries must cover requested period"):
        service.generate_quarterly_summary(
            user_id=user.id,
            start_date=start,
            end_date=dt.date(2026, 3, 31),
        )


def test_quarterly_summary_requires_calendar_month_summaries() -> None:
    user_id = uuid4()
    quarter_start = dt.date(2026, 1, 1)
    dailies = [
        _daily_memory_summary(
            user_id,
            quarter_start + dt.timedelta(days=offset),
            risk_flags=["high_sleep_debt"],
            readiness_state="low",
            offset=offset,
        )
        for offset in range(90)
    ]

    compiler = MemoryCompiler()
    monthlies = [
        compiler.compile_monthly(
            user_id=user_id,
            start_date=start,
            end_date=end,
            daily_summaries=[summary for summary in dailies if start <= summary.start_date <= end],
            weekly_summaries=[],
        )
        for start, end in [
            (dt.date(2026, 1, 1), dt.date(2026, 1, 30)),
            (dt.date(2026, 1, 31), dt.date(2026, 2, 28)),
            (dt.date(2026, 3, 1), dt.date(2026, 3, 31)),
        ]
    ]

    with pytest.raises(ValueError, match="monthly summaries must match calendar months"):
        compiler.compile_quarterly(
            user_id=user_id,
            start_date=quarter_start,
            end_date=dt.date(2026, 3, 31),
            monthly_summaries=monthlies,
        )


def test_monthly_compaction_excludes_sensitive_lower_horizon_text_and_preserves_refs() -> None:
    user_id = uuid4()
    start = dt.date(2026, 4, 1)
    dailies = [
        _daily_memory_summary(
            user_id,
            start + dt.timedelta(days=offset),
            risk_flags=["high_sleep_debt"],
            readiness_state="low",
            offset=offset,
            sensitive_text="SENSITIVE_LONG_TERM_NOTE_TOKEN",
            sensitive_fields_excluded=["daily_check_in.free_text_note_summary"],
        )
        for offset in range(3)
    ]

    monthly = MemoryCompiler().compile_monthly(
        user_id=user_id,
        start_date=start,
        end_date=start + dt.timedelta(days=2),
        daily_summaries=dailies,
        weekly_summaries=[],
    )
    serialized = json.dumps(monthly.model_dump(mode="json"), sort_keys=True)

    assert "SENSITIVE_LONG_TERM_NOTE_TOKEN" not in serialized
    assert monthly.sensitive_fields_excluded == ["daily_check_in.free_text_note_summary"]
    assert any(
        ref["table"] == "memory_summary" and ref["id"] == str(dailies[0].id)
        for ref in monthly.source_refs
    )
    for item in [*monthly.observations, *monthly.hypotheses]:
        assert item["source_refs"]


def test_quarterly_compaction_excludes_sensitive_monthly_text_and_preserves_refs() -> None:
    user_id = uuid4()
    quarter_start = dt.date(2026, 1, 1)
    sensitive_token = "SENSITIVE_QUARTERLY_MONTHLY_TOKEN"
    sensitive_field = "daily_check_in.free_text_note_summary"
    dailies = [
        _daily_memory_summary(
            user_id,
            quarter_start + dt.timedelta(days=offset),
            risk_flags=["high_sleep_debt"] if offset % 3 == 0 else [],
            readiness_state="low" if offset % 3 == 0 else "high",
            offset=offset,
            sensitive_text=sensitive_token,
            sensitive_fields_excluded=[sensitive_field],
        )
        for offset in range(90)
    ]

    compiler = MemoryCompiler()
    month_bounds = [
        (dt.date(2026, 1, 1), dt.date(2026, 1, 31)),
        (dt.date(2026, 2, 1), dt.date(2026, 2, 28)),
        (dt.date(2026, 3, 1), dt.date(2026, 3, 31)),
    ]
    monthlies = [
        compiler.compile_monthly(
            user_id=user_id,
            start_date=start,
            end_date=end,
            daily_summaries=[summary for summary in dailies if start <= summary.start_date <= end],
            weekly_summaries=[],
        )
        for start, end in month_bounds
    ]
    monthlies[0].observations.append(
        {
            "kind": "observation",
            "key": "monthly_sensitive_note_placeholder",
            "text": sensitive_token,
            "value": {"note": sensitive_token},
            "confidence": 0.2,
            "source_refs": [{"table": "memory_summary", "id": str(dailies[0].id)}],
        }
    )

    quarterly = compiler.compile_quarterly(
        user_id=user_id,
        start_date=quarter_start,
        end_date=dt.date(2026, 3, 31),
        monthly_summaries=monthlies,
    )
    serialized = json.dumps(quarterly.model_dump(mode="json"), sort_keys=True)

    assert sensitive_token not in serialized
    assert quarterly.sensitive_fields_excluded == [sensitive_field]
    assert any(
        ref["table"] == "memory_summary" and ref["id"] == str(monthlies[0].id)
        for ref in quarterly.source_refs
    )
    assert any(ref["table"] == "derived_daily_feature" for ref in quarterly.source_refs)
    for item in [*quarterly.observations, *quarterly.hypotheses]:
        assert item["source_refs"]


def test_memory_service_generates_monthly_and_quarterly_from_persisted_summaries(
    db_session: Session,
) -> None:
    user = _user(db_session)
    service = MemoryService(db_session)
    start = dt.date(2026, 1, 1)
    for offset in range(90):
        db_session.add(
            _daily_memory_summary(
                user.id,
                start + dt.timedelta(days=offset),
                risk_flags=["high_sleep_debt"] if offset % 3 == 0 else [],
                readiness_state="low" if offset % 3 == 0 else "high",
                offset=offset,
            )
        )
    db_session.commit()

    january = service.generate_monthly_summary(
        user_id=user.id,
        start_date=dt.date(2026, 1, 1),
        end_date=dt.date(2026, 1, 31),
    )
    service.generate_monthly_summary(
        user_id=user.id,
        start_date=dt.date(2026, 2, 1),
        end_date=dt.date(2026, 2, 28),
    )
    service.generate_monthly_summary(
        user_id=user.id,
        start_date=dt.date(2026, 3, 1),
        end_date=dt.date(2026, 3, 31),
    )
    quarterly = service.generate_quarterly_summary(
        user_id=user.id,
        start_date=dt.date(2026, 1, 1),
        end_date=dt.date(2026, 3, 31),
    )

    assert january.period_type == PeriodType.monthly
    assert quarterly.period_type == PeriodType.quarterly
    assert any(
        ref["table"] == "memory_summary" and ref["id"] == str(january.id)
        for ref in quarterly.source_refs
    )
    assert db_session.get(MemorySummary, quarterly.id) is not None


def test_memory_service_compaction_uses_latest_duplicate_period_summary(
    db_session: Session,
) -> None:
    user = _user(db_session)
    target_date = dt.date(2026, 1, 1)
    older = _daily_memory_summary(
        user.id,
        target_date,
        risk_flags=["high_sleep_debt"],
        readiness_state="low",
        offset=1,
    )
    older.created_at = dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC)
    newer = _daily_memory_summary(
        user.id,
        target_date,
        risk_flags=["high_training_density"],
        readiness_state="high",
        offset=2,
    )
    newer.created_at = dt.datetime(2026, 1, 1, 9, 0, tzinfo=dt.UTC)
    db_session.add_all([older, newer])
    db_session.commit()

    monthly = MemoryService(db_session).generate_monthly_summary(
        user_id=user.id,
        start_date=target_date,
        end_date=target_date,
    )

    risk_patterns = next(
        item for item in monthly.observations if item["key"] == "monthly_risk_patterns"
    )
    assert risk_patterns["value"]["risk_flag_counts"] == {"high_training_density": 1}
    assert any(
        ref["table"] == "memory_summary" and ref["id"] == str(newer.id)
        for ref in monthly.source_refs
    )
    assert not any(
        ref["table"] == "memory_summary" and ref["id"] == str(older.id)
        for ref in monthly.source_refs
    )


def test_memory_correction_and_deletion_are_audited(db_session: Session) -> None:
    user = _user(db_session)
    summary = MemorySummary(
        user_id=user.id,
        period_type=PeriodType.daily,
        start_date=dt.date(2026, 7, 4),
        end_date=dt.date(2026, 7, 4),
        summary_version="memory-summary-v1",
        observations=[
            {
                "kind": "observation",
                "key": "readiness_assessment",
                "text": "Original observation.",
                "confidence": 0.7,
                "source_refs": [{"table": "readiness_assessment", "id": "assessment-1"}],
            }
        ],
        hypotheses=[],
        confidence=0.7,
        source_refs=[
            {"table": "readiness_assessment", "id": "assessment-1"},
            {"table": "source_sample", "source_id": "sample-aggregate"},
        ],
        sensitive_fields_excluded=[],
    )
    db_session.add(summary)
    db_session.flush()

    service = MemoryService(db_session)
    corrected = service.correct_summary(
        summary.id,
        observations=[
            {
                "kind": "observation",
                "key": "readiness_assessment",
                "text": "Corrected observation.",
                "confidence": 2.0,
                "source_refs": [{"table": "readiness_assessment", "id": "assessment-1"}],
            }
        ],
        hypotheses=[
            {
                "kind": "hypothesis",
                "key": "corrected_hypothesis",
                "text": "Corrected hypothesis.",
                "confidence": -1.0,
                "source_refs": [{"table": "readiness_assessment", "id": "assessment-1"}],
            }
        ],
        actor="user",
    )
    assert corrected.observations[0]["confidence"] == 1.0
    assert corrected.hypotheses[0]["confidence"] == 0.0
    assert {"table": "source_sample", "source_id": "sample-aggregate"} in corrected.source_refs
    service.delete_summary(corrected.id, actor="user")

    assert db_session.get(MemorySummary, summary.id) is None
    events = list(db_session.exec(select(AuditEvent).order_by(AuditEvent.timestamp)).all())
    assert [event.event_type for event in events] == [
        AuditEventType.memory_corrected,
        AuditEventType.memory_deleted,
    ]
    assert all(event.redaction_status == RedactionStatus.redacted for event in events)
    assert events[0].event_metadata["changed_fields"] == ["observations", "hypotheses"]
    assert {"table": "source_sample", "source_id": "sample-aggregate"} in events[0].event_metadata[
        "source_refs"
    ]
    assert events[1].event_metadata["memory_summary_id"] == str(summary.id)
    assert {"table": "source_sample", "source_id": "sample-aggregate"} in events[1].event_metadata[
        "source_refs"
    ]
    assert (
        service.recent_for_reasoning(
            user_id=user.id,
            target_date=dt.date(2026, 7, 5),
        )
        == []
    )


def test_memory_correction_rejects_unstructured_source_refs(db_session: Session) -> None:
    user = _user(db_session)
    summary = MemorySummary(
        user_id=user.id,
        period_type=PeriodType.daily,
        start_date=dt.date(2026, 7, 4),
        end_date=dt.date(2026, 7, 4),
        summary_version="memory-summary-v1",
        observations=[
            {
                "kind": "observation",
                "key": "readiness_assessment",
                "text": "Original observation.",
                "confidence": 0.7,
                "source_refs": [{"table": "readiness_assessment", "id": "assessment-1"}],
            }
        ],
        hypotheses=[],
        confidence=0.7,
        source_refs=[{"table": "readiness_assessment", "id": "assessment-1"}],
        sensitive_fields_excluded=[],
    )
    db_session.add(summary)
    db_session.flush()

    with pytest.raises(ValueError, match="structured source_refs"):
        MemoryService(db_session).correct_summary(
            summary.id,
            observations=[
                {
                    "kind": "observation",
                    "key": "invalid_ref",
                    "text": "Invalid source ref.",
                    "confidence": 0.7,
                    "source_refs": ["not-a-ref"],
                }
            ],
        )


def test_recent_summary_accessor_returns_structured_memory_before_target_date(
    db_session: Session,
) -> None:
    user = _user(db_session)
    current = dt.date(2026, 7, 4)
    older = MemorySummary(
        user_id=user.id,
        period_type=PeriodType.weekly,
        start_date=current - dt.timedelta(days=7),
        end_date=current - dt.timedelta(days=1),
        summary_version="memory-summary-v1",
        observations=[
            {
                "kind": "observation",
                "key": "weekly_readiness_arc",
                "text": "Illness disrupted two training days.",
                "confidence": 0.8,
                "source_refs": [{"table": "daily_check_in", "id": "checkin-1"}],
            }
        ],
        hypotheses=[],
        confidence=0.8,
        source_refs=[{"table": "daily_check_in", "id": "checkin-1"}],
        sensitive_fields_excluded=[],
    )
    same_day = MemorySummary(
        user_id=user.id,
        period_type=PeriodType.daily,
        start_date=current,
        end_date=current,
        summary_version="memory-summary-v1",
        observations=[
            {
                "kind": "observation",
                "key": "readiness_assessment",
                "text": "This should not be visible yet.",
                "confidence": 0.8,
                "source_refs": [{"table": "readiness_assessment", "id": "assessment-current"}],
            }
        ],
        hypotheses=[],
        confidence=0.8,
        source_refs=[{"table": "readiness_assessment", "id": "assessment-current"}],
        sensitive_fields_excluded=[],
    )
    db_session.add_all([older, same_day])
    db_session.flush()

    recent = MemoryService(db_session).recent_for_reasoning(
        user_id=user.id,
        target_date=current,
    )

    assert len(recent) == 1
    assert recent[0]["memory_summary_id"] == str(older.id)
    assert recent[0]["period_type"] == "weekly"
    assert "Illness disrupted" in recent[0]["observation"]
    assert recent[0]["source_refs"] == older.source_refs
