"""Contract tests for the published API and recommendation schemas."""

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.schemas.api import (
    AssistantQueryRequest,
    AssistantQueryResponse,
    DailyAnalysisRequest,
    DailyAnalysisResponse,
    DailyBriefingResponse,
    DailyCheckInDetailResponse,
    DailyCheckInRequest,
    DailyCheckInResponse,
    DataExportRequest,
    DataExportResponse,
    GoalRequest,
    HealthSyncRequest,
    HealthSyncResponse,
    RecommendationFeedbackRequest,
    RecommendationFeedbackResponse,
)
from baseline_api.schemas.recommendation import RecommendationContract

FIXED_UUID = UUID("11111111-1111-4111-8111-111111111111")
FIXED_TRACE_UUID = UUID("22222222-2222-4222-8222-222222222222")
FIXED_DATE = date(2026, 1, 15)
FIXED_DATETIME = datetime(2026, 1, 15, 8, 30, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )


def _round_trip(model_type: type[BaseModel], payload: dict[str, Any]) -> None:
    model = model_type.model_validate(payload)
    dumped = model.model_dump_json()
    assert model_type.model_validate_json(dumped) == model


def _contract_cases() -> Iterable[tuple[type[BaseModel], dict[str, Any]]]:
    sample = {
        "source_sample_id": "hk-hrv-1",
        "sample_type": "heart_rate_variability",
        "start_time": FIXED_DATETIME.isoformat(),
        "end_time": FIXED_DATETIME.isoformat(),
        "value": 52.4,
        "unit": "ms",
        "source_metadata": {"source": "apple_health"},
    }
    evidence = {
        "metric": "hrv_deviation",
        "value": "+9%",
        "interpretation": "favorable relative to baseline",
        "source": "derived_daily_feature",
    }
    citation = {
        "title": "Training load review",
        "source": "knowledge_corpus",
        "url": "https://example.com/training-load",
        "cited_claim": "Training load should be adjusted when recovery signals conflict.",
    }
    data_quality_summary = {
        "status": "ok",
        "notes": [{"metric": "hrv", "note": "Recent sample available.", "severity": "info"}],
    }

    return (
        (
            HealthSyncRequest,
            {
                "client_sync_id": "sync-client-1",
                "device_id": "watch-1",
                "timezone": "Asia/Shanghai",
                "samples": [sample],
                "last_anchor": "anchor-1",
                "consent_version": "2026-01",
            },
        ),
        (
            HealthSyncResponse,
            {
                "sync_id": str(FIXED_UUID),
                "accepted_count": 1,
                "duplicate_count": 0,
                "rejected_count": 0,
                "warnings": [],
                "next_anchor": "anchor-2",
                "data_quality_summary": data_quality_summary,
            },
        ),
        (
            DailyCheckInRequest,
            {
                "date": FIXED_DATE.isoformat(),
                "energy_score": 7,
                "mood_score": 7,
                "soreness_score": 4,
                "stress_score": 3,
                "perceived_recovery_score": 6,
                "food_quality_score": 8,
                "flags": {"alcohol": False, "illness": False, "injury": False, "travel": True},
                "structured_notes": {"training": "upper body"},
                "free_text_note": "Felt a little flat in the morning.",
                "sensitive_note_policy": "exclude_from_external_llm",
            },
        ),
        (
            DailyCheckInResponse,
            {
                "checkin_id": str(FIXED_UUID),
                "accepted_fields": ["energy_score", "mood_score"],
                "redaction_status": "partial",
                "analysis_job_id": str(FIXED_TRACE_UUID),
            },
        ),
        (
            DailyCheckInDetailResponse,
            {
                "checkin_id": str(FIXED_UUID),
                "request": {
                    "date": FIXED_DATE.isoformat(),
                    "energy_score": 7,
                    "flags": {"travel": True},
                    "structured_notes": {"private_lifestyle_indicator": True},
                    "free_text_note": None,
                    "sensitive_note_policy": "exclude_from_external_llm",
                },
                "has_free_text_note": True,
            },
        ),
        (
            DailyAnalysisRequest,
            {
                "date": FIXED_DATE.isoformat(),
                "force_recompute": False,
                "include_external_knowledge": False,
                "privacy_mode": "local_only",
            },
        ),
        (
            DailyAnalysisResponse,
            {
                "analysis_job_id": str(FIXED_UUID),
                "status": "queued",
                "estimated_completion_seconds": 30,
            },
        ),
        (
            DailyBriefingResponse,
            {
                "date": FIXED_DATE.isoformat(),
                "readiness_state": "mixed",
                "confidence": "medium",
                "data_freshness": {
                    "latest_sample_at": FIXED_DATETIME.isoformat(),
                    "latest_checkin_date": FIXED_DATE.isoformat(),
                    "stale_sources": [],
                },
                "evidence": [evidence],
                "recommendation_band": "moderate_or_upper_body",
                "candidate_options": [
                    {
                        "label": "Upper body strength",
                        "recommendation_band": "moderate_or_upper_body",
                        "rationale": "Preserves training while reducing lower-body load.",
                    }
                ],
                "goal_tradeoffs": [{"goal": "VO2 max", "tradeoff": "Delay intervals one day."}],
                "uncertainty": ["No soreness check-in was available."],
                "safety_notes": ["This is wellness decision support, not medical advice."],
                "trace_id": str(FIXED_TRACE_UUID),
                "generated_at": FIXED_DATETIME.isoformat(),
            },
        ),
        (
            AssistantQueryRequest,
            {
                "question": "Why not intervals today?",
                "date_context": FIXED_DATE.isoformat(),
                "allowed_data_scope": ["briefing_trace", "recent_health"],
                "include_external_knowledge": False,
                "privacy_mode": "local_only",
            },
        ),
        (
            AssistantQueryResponse,
            {
                "answer": "Sleep debt and recent lower-body load make intervals less attractive.",
                "personal_evidence": [evidence],
                "external_sources": [citation],
                "confidence": "medium",
                "uncertainty": ["No soreness check-in was available."],
                "safety_status": "passed",
                "trace_id": str(FIXED_TRACE_UUID),
            },
        ),
        (
            RecommendationFeedbackRequest,
            {
                "rating": "useful",
                "action_taken": "followed",
                "reason": "Matched how I felt.",
                "outcome_notes": "Session felt appropriate.",
            },
        ),
        (
            RecommendationFeedbackResponse,
            {
                "feedback_id": str(FIXED_UUID),
                "memory_update_status": "queued",
                "eval_queue_status": "queued",
            },
        ),
        (
            DataExportRequest,
            {
                "export_scope": "all",
                "format": "json",
                "include_raw_data": False,
                "include_model_traces": True,
            },
        ),
        (
            DataExportResponse,
            {
                "export_job_id": str(FIXED_UUID),
                "status": "queued",
                "expires_at": FIXED_DATETIME.isoformat(),
            },
        ),
    )


@pytest.mark.parametrize(("model_type", "payload"), _contract_cases())
def test_api_contracts_round_trip(model_type: type[BaseModel], payload: dict[str, Any]) -> None:
    _round_trip(model_type, payload)


def test_prd_recommendation_example_validates() -> None:
    payload = {
        "readiness_state": "mixed",
        "recommendation_band": "moderate_or_upper_body",
        "confidence": "medium",
        "personal_evidence": [
            {
                "metric": "hrv_deviation",
                "value": "+9%",
                "interpretation": "favorable relative to baseline",
            },
            {
                "metric": "sleep_debt",
                "value": "1.8h",
                "interpretation": "unfavorable",
            },
        ],
        "risk_flags": ["three_lower_body_sessions_in_six_days"],
        "recommendation": {
            "primary": "Prefer upper-body strength or zone 2 work today.",
            "avoid": (
                "Postpone VO2 max interval work unless subjective energy is unusually high "
                "and soreness is low."
            ),
        },
        "uncertainty": ["No soreness check-in was available."],
        "safety_status": "passed",
        "safety_note": "This is wellness decision support, not medical advice.",
        "safety_result": {"status": "passed", "policy_version": "test"},
    }

    contract = RecommendationContract.model_validate(payload)

    assert contract.schema_version == "v1"
    assert contract.safety_status == "passed"


@pytest.mark.parametrize(
    "field_name",
    [
        "personal_evidence",
        "confidence",
        "uncertainty",
        "safety_status",
        "safety_note",
        "safety_result",
    ],
)
def test_recommendation_rejects_missing_mandatory_fields(field_name: str) -> None:
    payload = {
        "readiness_state": "mixed",
        "recommendation_band": "moderate_or_upper_body",
        "confidence": "medium",
        "personal_evidence": [
            {
                "metric": "hrv_deviation",
                "value": "+9%",
                "interpretation": "favorable relative to baseline",
            }
        ],
        "recommendation": {"primary": "Prefer upper-body strength or zone 2 work today."},
        "uncertainty": ["No soreness check-in was available."],
        "safety_status": "passed",
        "safety_note": "This is wellness decision support, not medical advice.",
        "safety_result": {"status": "passed", "policy_version": "test"},
    }
    payload.pop(field_name)

    with pytest.raises(ValidationError):
        RecommendationContract.model_validate(payload)


@pytest.mark.parametrize(
    "constraints",
    [
        {"sexual_health": "medication dose 50mg"},
        {"notes": "diagnosis should improve"},
        {"lifestyle": "symptom tracking"},
    ],
)
def test_goal_request_rejects_clinical_constraint_values(
    constraints: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        GoalRequest.model_validate(
            {
                "category": "long_term_wellness",
                "priority": 3,
                "time_horizon": "medium_term",
                "success_metric": "high-level lifestyle consistency",
                "constraints": constraints,
            }
        )


def test_goal_request_rejects_clinical_success_metric() -> None:
    with pytest.raises(ValidationError):
        GoalRequest.model_validate(
            {
                "category": "long_term_wellness",
                "priority": 3,
                "time_horizon": "medium_term",
                "success_metric": "reduce erectile dysfunction symptoms",
                "constraints": {},
            }
        )


def test_data_export_route_is_published() -> None:
    schema = create_app(_settings()).openapi()

    assert schema["paths"]["/v1/data/export"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/DataExportRequest"}
    assert schema["paths"]["/v1/data/export/{export_job_id}/file"]["get"]["responses"]["200"][
        "content"
    ] == {
        "application/octet-stream": {
            "schema": {"type": "string", "format": "binary"},
        }
    }


def test_openapi_snapshot_is_current() -> None:
    snapshot_path = Path("docs/architecture/openapi.json")
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
    actual = create_app(_settings()).openapi()

    assert actual == expected
