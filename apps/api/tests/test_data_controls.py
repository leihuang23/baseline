"""Tests for P4-04 data controls, consent, and model disclosure."""

from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
from collections.abc import Generator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from baseline_api.api.v1.health import get_normalization_queue
from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.db.models import (
    AuditEvent,
    ConsentRecord,
    DailyAnalysisJob,
    DailyCheckIn,
    Goal,
    MemorySummary,
    ModelRun,
    RawHealthSample,
    ReadinessAssessment,
    ReasoningTrace,
    Recommendation,
    User,
)
from baseline_api.db.models.enums import (
    AuditEventType,
    ConfidenceLevel,
    GoalCategory,
    MetricType,
    PeriodType,
    PrivacyMode,
    ReadinessState,
    RecommendationBand,
    RecommendationType,
    RedactionStatus,
    RunType,
    SafetyStatus,
    SensitiveNotePolicy,
    TimeHorizon,
)
from baseline_api.db.session import get_db_session
from baseline_api.llm.modelrun_logger import ModelRunLogger, minimized_payload_metadata
from baseline_api.llm.orchestrator import LLMOrchestrator
from baseline_api.llm.providers import ProviderRequest, ProviderResponse
from baseline_api.llm.router import ModelRouter
from baseline_api.llm.schemas import PromptInputs, TaskType
from baseline_api.privacy import LocalExportStore
from baseline_api.privacy.export import decrypt_bytes, encrypt_bytes
from baseline_api.privacy.model_runs import (
    model_run_ids_from_payload,
    sanitize_model_input_metadata,
)


class FakeNormalizationQueue:
    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str:
        return f"job:{import_batch_id}:{user_id}"


@dataclass
class FakeProvider:
    name: str = "external-provider"
    calls: list[ProviderRequest] = field(default_factory=list)
    requires_external_llm_consent: bool = True

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            provider=self.name,
            model=request.model,
            content=json.dumps(
                {
                    "schema_version": "llm_explanation_v1",
                    "summary": "Use deterministic guidance.",
                    "rationale": ["Structured features support a moderate plan."],
                    "uncertainty": ["Subjective context can change this."],
                    "personal_evidence_refs": ["sleep_features.values.sleep_debt_hours"],
                    "external_citations": [],
                    "safety_boundary_acknowledged": True,
                    "no_diagnosis_or_treatment_claims": True,
                }
            ),
            token_usage={},
            cost=None,
            latency_ms=1,
        )


def test_minimized_payload_metadata_hashes_message_values_without_raw_pii() -> None:
    raw_pii = "private travel note and raw sample identifier"
    raw_key = "private travel note key"

    metadata = minimized_payload_metadata(
        {
            "provider": "external-provider",
            "model": "model-a",
            "prompt_version": "prompt-v1",
            "schema_version": "schema-v1",
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task_type": "simple_explanation",
                            "structured_feature_assessment": {
                                "readiness_state": "moderate",
                                raw_key: raw_pii,
                            },
                        }
                    ),
                }
            ],
        }
    )

    rendered = json.dumps(metadata, sort_keys=True)
    assert raw_pii not in rendered
    assert raw_key not in rendered
    assert metadata["message_count"] == 1
    assert metadata["messages"][0]["content_hash"]
    content_shape = metadata["messages"][0]["content_shape"]
    assert content_shape["field_count"] == 2
    assert content_shape["fields"][0]["key_hash"]
    disclosure = metadata["disclosure_payload"]["messages"][0]["content"]
    assert disclosure["task_type"] == "simple_explanation"
    assert disclosure["structured_feature_assessment"]["readiness_state"] == "moderate"
    assert disclosure["structured_feature_assessment"]["_redacted_fields"][0]["key_hash"]
    assert disclosure["structured_feature_assessment"]["_redacted_fields"][0]["value"]["hash"]


def test_model_input_metadata_sanitizer_redacts_legacy_raw_values() -> None:
    raw_pii = "raw legacy prompt"

    metadata = sanitize_model_input_metadata(
        {
            "raw_prompt": raw_pii,
            "messages": [
                {
                    "role": "user",
                    "content_hash": "content-hash",
                    "content_disclosure": {"secret_note": raw_pii},
                }
            ],
            "disclosure_payload": {
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "task_type": "simple_explanation",
                            "secret_note": raw_pii,
                        },
                    }
                ]
            },
        }
    )

    rendered = json.dumps(metadata, sort_keys=True)
    assert raw_pii not in rendered
    assert "raw_prompt" not in metadata
    assert "content_disclosure" not in metadata["messages"][0]
    disclosure = metadata["disclosure_payload"]["messages"][0]["content"]
    assert disclosure["task_type"] == "simple_explanation"
    assert disclosure["_redacted_fields"][0]["key_hash"]
    assert disclosure["_redacted_fields"][0]["value"]["hash"]


def test_model_input_metadata_sanitizer_preserves_hashed_redaction_descriptors() -> None:
    raw_pii = "private travel note and raw sample identifier"
    raw_key = "private model payload key"

    metadata = sanitize_model_input_metadata(
        minimized_payload_metadata(
            {
                "provider": "external-provider",
                "model": "model-a",
                "prompt_version": "prompt-v1",
                "schema_version": "schema-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task_type": "simple_explanation",
                                "structured_feature_assessment": {
                                    "readiness_state": "moderate",
                                    raw_key: raw_pii,
                                },
                            }
                        ),
                    }
                ],
            }
        )
    )

    rendered = json.dumps(metadata, sort_keys=True)
    assert raw_pii not in rendered
    assert raw_key not in rendered
    disclosure = metadata["disclosure_payload"]["messages"][0]["content"]
    redacted_value = disclosure["structured_feature_assessment"]["_redacted_fields"][0]["value"]
    assert redacted_value["type"] == "string"
    assert redacted_value["character_count"] == len(raw_pii)
    assert redacted_value["hash"]


def test_model_run_id_extractor_collects_retry_and_fallback_ids() -> None:
    primary_id = uuid4()
    fallback_id = uuid4()

    ids = model_run_ids_from_payload(
        {
            "model_run_id": str(primary_id),
            "briefing_generation": {
                "model_run_ids": [
                    str(primary_id),
                    str(fallback_id),
                    "not-a-uuid",
                ]
            },
        }
    )

    assert ids == [primary_id, fallback_id]


def test_export_encryption_uses_authenticated_aead() -> None:
    key = b"0" * 32
    plaintext = b"Private note summarized locally."

    encrypted = encrypt_bytes(plaintext, key)

    assert b"Private note summarized locally" not in encrypted
    assert decrypt_bytes(encrypted, key) == plaintext
    tampered = bytearray(encrypted)
    tampered[-1] ^= 1
    with pytest.raises(ValueError, match="authentication tag"):
        decrypt_bytes(bytes(tampered), key)


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )


def _client(db_session: Session, export_store: LocalExportStore | None = None) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_normalization_queue] = lambda: FakeNormalizationQueue()
    if export_store is not None:
        app.state.export_store = export_store
    return TestClient(app)


def _seed_user(
    db_session: Session,
    *,
    consent_version: str = "v1",
    categories: list[str] | None = None,
    cloud_processing_enabled: bool = True,
    external_llm_enabled: bool = True,
    raw_note_processing_enabled: bool = False,
) -> User:
    user = User(
        privacy_mode=PrivacyMode.cloud_assisted,
        active_consent_version=consent_version,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version=consent_version,
            health_categories_enabled=categories or ["all"],
            cloud_processing_enabled=cloud_processing_enabled,
            external_llm_enabled=external_llm_enabled,
            raw_note_processing_enabled=raw_note_processing_enabled,
            timestamp=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
        )
    )
    db_session.flush()
    return user


def _decrypt_response_export(encrypted: bytes, data: dict[str, object]) -> bytes:
    encryption = data["encryption"]
    assert isinstance(encryption, dict)
    key_base64 = encryption["key_base64"]
    assert isinstance(key_base64, str)
    return decrypt_bytes(encrypted, base64.b64decode(key_base64))


def _seed_checkin(db_session: Session, user: User) -> DailyCheckIn:
    checkin = DailyCheckIn(
        user_id=user.id,
        date=dt.date(2026, 7, 4),
        energy_score=6,
        sensitive_note_policy=SensitiveNotePolicy.summarize_before_external_llm,
        redaction_status=RedactionStatus.partial,
        structured_notes={"training": "upper body"},
        free_text_note_reference="summarize_before_external_llm:hash-only",
        free_text_note_summary="Private note summarized locally.",
    )
    db_session.add(checkin)
    db_session.flush()
    return checkin


def _seed_memory(db_session: Session, user: User) -> MemorySummary:
    memory = MemorySummary(
        user_id=user.id,
        period_type=PeriodType.weekly,
        start_date=dt.date(2026, 6, 29),
        end_date=dt.date(2026, 7, 5),
        summary_version="v1",
        observations=[{"metric": "sleep", "summary": "stable"}],
        hypotheses=[],
    )
    db_session.add(memory)
    db_session.flush()
    return memory


def _seed_raw_sample(db_session: Session, user: User) -> RawHealthSample:
    sample = RawHealthSample(
        user_id=user.id,
        source_platform="apple_health",
        source_device="watch",
        source_sample_id="hk-raw-export",
        content_hash="hash",
        sample_type=MetricType.heart_rate_variability,
        start_time=dt.datetime(2026, 7, 4, 7, 0, tzinfo=dt.UTC),
        raw_value=51.0,
        raw_unit="ms",
        source_metadata={"source": "synthetic"},
        imported_at=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
        import_batch_id=uuid4(),
    )
    db_session.add(sample)
    db_session.flush()
    return sample


def _seed_goal(db_session: Session, user: User) -> Goal:
    goal = Goal(
        user_id=user.id,
        category=GoalCategory.vo2_max,
        priority=1,
        time_horizon=TimeHorizon.medium_term,
        success_metric="private-priority",
        constraints={"training_days": 4},
    )
    db_session.add(goal)
    db_session.flush()
    return goal


def _seed_checkin_derived_artifacts(
    db_session: Session,
    user: User,
    checkin: DailyCheckIn,
) -> dict[str, UUID]:
    trace_id = uuid4()
    model_run = ModelRun(
        user_id=user.id,
        run_type=RunType.explanation,
        model_provider="external-provider",
        model_name="model-a",
        prompt_version="prompt-v1",
        input_hash="input-hash",
        output_hash="output-hash",
        schema_version="schema-v1",
        token_usage={},
        safety_result={
            "status": "passed",
            "review_note": "Private knee note must stay hashed.",
        },
        input_metadata={
            "disclosure_payload": {"messages": [{"content": {"readiness_state": "moderate"}}]}
        },
    )
    db_session.add(model_run)
    db_session.flush()
    fallback_model_run = ModelRun(
        user_id=user.id,
        run_type=RunType.explanation,
        model_provider="fallback-provider",
        model_name="model-b",
        prompt_version="prompt-v1",
        input_hash="fallback-input-hash",
        output_hash="fallback-output-hash",
        schema_version="schema-v1",
        token_usage={},
        safety_result={
            "status": "schema_invalid",
            "review_note": "Private fallback note must stay hashed.",
        },
        input_metadata={
            "disclosure_payload": {"messages": [{"content": {"readiness_state": "moderate"}}]}
        },
    )
    db_session.add(fallback_model_run)
    db_session.flush()

    db_session.add(
        ReasoningTrace(
            id=trace_id,
            user_id=user.id,
            date=checkin.date,
            trace_version="trace-v1",
            assessment_version="assessment-v1",
            input_hash="trace-input-hash",
            rules_fired=[{"source": "daily_check_in.energy_score"}],
            hard_safety_flags=[],
            trace_payload={
                "reasoning_trace_id": str(trace_id),
                "daily_check_in": {"energy_score": checkin.energy_score},
                "briefing_generation": {
                    "model_run_ids": [str(model_run.id), str(fallback_model_run.id)]
                },
            },
        )
    )
    db_session.add(
        ReadinessAssessment(
            user_id=user.id,
            date=checkin.date,
            assessment_version="assessment-v1",
            readiness_state=ReadinessState.moderate,
            recommendation_band=RecommendationBand.moderate,
            confidence=ConfidenceLevel.medium,
            uncertainty=[],
            evidence_items=[{"source": "daily_check_in.energy_score"}],
            risk_flags=[],
            candidate_options=[],
            follow_up_questions=[],
            goal_tradeoffs=[],
            hard_safety_flags=[],
            reasoning_trace_id=trace_id,
        )
    )
    recommendation = Recommendation(
        user_id=user.id,
        date=checkin.date,
        recommendation_type=RecommendationType.training,
        recommendation_text="Moderate day.",
        candidate_options=[],
        evidence_refs=[{"source": "daily_check_in.energy_score"}],
        safety_status=SafetyStatus.passed,
        safety_result={"status": "passed"},
        model_run_id=model_run.id,
        reasoning_trace_id=trace_id,
        briefing_payload={"evidence": [{"source": "daily_check_in.energy_score"}]},
    )
    db_session.add(recommendation)
    db_session.flush()
    db_session.add(
        DailyAnalysisJob(
            user_id=user.id,
            date=checkin.date,
            status="completed",
            force_recompute=False,
            include_external_knowledge=False,
            privacy_mode=PrivacyMode.cloud_assisted.value,
            request_trace_id="trace-request",
            reasoning_trace_id=trace_id,
            recommendation_id=recommendation.id,
            stage_trace=[],
        )
    )
    memory = MemorySummary(
        user_id=user.id,
        period_type=PeriodType.daily,
        start_date=checkin.date,
        end_date=checkin.date,
        summary_version="v1",
        observations=[
            {
                "kind": "observation",
                "key": "daily_check_in_structured_signals",
                "text": "Check-in structured scores: energy_score=6.",
                "confidence": 0.7,
                "source_refs": [{"table": "daily_check_in", "id": str(checkin.id)}],
            }
        ],
        hypotheses=[],
        source_refs=[{"table": "daily_check_in", "id": str(checkin.id)}],
    )
    db_session.add(memory)
    db_session.flush()
    return {
        "trace_id": trace_id,
        "model_run_id": model_run.id,
        "fallback_model_run_id": fallback_model_run.id,
        "memory_id": memory.id,
    }


def _seed_assistant_trace(
    db_session: Session,
    user: User,
    trace_date: dt.date,
    *,
    model_run_id: UUID | None = None,
) -> ReasoningTrace:
    trace_payload: dict[str, object] = {
        "assistant_queries": [
            {
                "question": "Can I run with my private knee concern?",
                "answer": "Private assistant answer.",
            }
        ]
    }
    if model_run_id is not None:
        trace_payload["model_run_id"] = str(model_run_id)

    trace = ReasoningTrace(
        user_id=user.id,
        date=trace_date,
        trace_version="assistant_query_v1",
        assessment_version="assistant_query_v1",
        input_hash="assistant-query-input-hash",
        rules_fired=[],
        hard_safety_flags=[],
        trace_payload=trace_payload,
    )
    db_session.add(trace)
    db_session.flush()
    return trace


def _prompt_inputs() -> PromptInputs:
    return PromptInputs(
        task_type=TaskType.simple_explanation,
        deterministic_assessment={
            "readiness_state": "moderate",
            "recommendation_band": "moderate",
            "confidence": "medium",
            "uncertainty": ["No soreness check-in today."],
            "evidence_items": [],
            "risk_flags": [],
            "hard_safety_flags": [],
        },
        derived_features={"sleep_features": {"values": {"sleep_debt_hours": 0.3}}},
        retrieved_evidence=[{"id": "evidence-1", "claim": "Recent sleep debt was low."}],
        external_knowledge=[],
        raw_samples=[{"secret": "raw sample must not leave"}],
    )


def test_export_creates_encrypted_expiring_file_with_requested_scope(
    db_session: Session,
    tmp_path,
) -> None:
    user = _seed_user(db_session)
    _seed_checkin(db_session, user)
    _seed_memory(db_session, user)
    _seed_raw_sample(db_session, user)
    export_store = LocalExportStore(tmp_path)
    client = _client(db_session, export_store)

    response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "checkins",
            "format": "json",
            "include_raw_data": True,
            "include_model_traces": False,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ready"
    assert data["download_url"].startswith("/v1/data/export/")
    assert data["encryption"]["algorithm"] == "AES-256-GCM"
    assert data["encryption"]["key_base64"]

    encrypted = client.get(data["download_url"]).content
    assert b"Private note summarized locally" not in encrypted
    job_id = UUID(data["export_job_id"])
    decrypted = json.loads(_decrypt_response_export(encrypted, data))
    assert set(decrypted["sections"]) == {"daily_check_ins"}
    assert decrypted["sections"]["daily_check_ins"][0]["structured_notes"] == {
        "training": "upper body"
    }

    restarted_client = _client(db_session, LocalExportStore(tmp_path))
    restarted_encrypted = restarted_client.get(data["download_url"])
    assert restarted_encrypted.status_code == 200
    assert _decrypt_response_export(restarted_encrypted.content, data) == _decrypt_response_export(
        encrypted,
        data,
    )

    stored = export_store.get(job_id)
    tampered = bytearray(stored.path.read_bytes())
    tampered[-1] ^= 1
    stored.path.write_bytes(tampered)
    key = base64.b64decode(data["encryption"]["key_base64"])
    with pytest.raises(ValueError, match="authentication tag"):
        export_store.decrypt(job_id, key)

    object.__setattr__(stored, "expires_at", dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1))
    expired = client.get(data["download_url"])
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "export_expired"
    assert not stored.path.exists()


def test_export_store_cleans_up_expired_manifests_and_encrypted_files(tmp_path) -> None:
    store = LocalExportStore(tmp_path, retention_hours=1)
    stored, _key = store.create(
        b'{"sections": {}}',
        user_id=uuid4(),
        now=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
    )

    removed = store.cleanup_expired(now=dt.datetime(2026, 7, 4, 9, 1, tzinfo=dt.UTC))

    assert removed == 1
    assert not stored.path.exists()
    assert not (tmp_path / f"{stored.job_id}.export.json").exists()


def test_csv_export_decrypts_to_requested_scope(db_session: Session, tmp_path) -> None:
    user = _seed_user(db_session)
    _seed_checkin(db_session, user)
    _seed_memory(db_session, user)
    export_store = LocalExportStore(tmp_path)
    client = _client(db_session, export_store)

    response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "checkins",
            "format": "csv",
            "include_raw_data": False,
            "include_model_traces": False,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    plaintext = _decrypt_response_export(client.get(data["download_url"]).content, data).decode()
    rows = list(csv.DictReader(io.StringIO(plaintext)))
    assert [row["section"] for row in rows] == ["daily_check_ins"]
    assert "Private note summarized locally." in rows[0]["record_json"]
    assert "memory_summaries" not in plaintext


def test_health_export_scope_excludes_goals(db_session: Session, tmp_path) -> None:
    user = _seed_user(db_session)
    _seed_goal(db_session, user)
    _seed_raw_sample(db_session, user)
    export_store = LocalExportStore(tmp_path)
    client = _client(db_session, export_store)

    response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "health",
            "format": "json",
            "include_raw_data": False,
            "include_model_traces": False,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    decrypted = json.loads(_decrypt_response_export(client.get(data["download_url"]).content, data))
    assert "raw_health_samples" not in decrypted["sections"]
    assert "goals" not in decrypted["sections"]
    assert "private-priority" not in json.dumps(decrypted, sort_keys=True)


def test_all_export_scope_includes_goals(db_session: Session, tmp_path) -> None:
    user = _seed_user(db_session)
    _seed_goal(db_session, user)
    export_store = LocalExportStore(tmp_path)
    client = _client(db_session, export_store)

    response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "all",
            "format": "json",
            "include_raw_data": False,
            "include_model_traces": False,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    decrypted = json.loads(_decrypt_response_export(client.get(data["download_url"]).content, data))
    assert decrypted["sections"]["goals"][0]["success_metric"] == "private-priority"
    assert decrypted["sections"]["goals"][0]["constraints"] == {"training_days": 4}


def test_scoped_export_filters_included_model_traces(db_session: Session, tmp_path) -> None:
    user = _seed_user(db_session)
    checkin = _seed_checkin(db_session, user)
    _seed_checkin_derived_artifacts(db_session, user, checkin)
    db_session.add(
        ModelRun(
            user_id=user.id,
            run_type=RunType.explanation,
            model_provider="unrelated-provider",
            model_name="model-z",
            prompt_version="prompt-v1",
            input_hash="unrelated-input-hash",
            output_hash="unrelated-output-hash",
            schema_version="schema-v1",
            token_usage={},
            safety_result={"status": "passed"},
            input_metadata={
                "disclosure_payload": {"messages": [{"content": {"readiness_state": "low"}}]}
            },
        )
    )
    db_session.flush()
    export_store = LocalExportStore(tmp_path)
    client = _client(db_session, export_store)

    checkins_response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "checkins",
            "format": "json",
            "include_raw_data": False,
            "include_model_traces": True,
        },
    )
    health_response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "health",
            "format": "json",
            "include_raw_data": False,
            "include_model_traces": True,
        },
    )

    assert checkins_response.status_code == 200
    checkins_data = checkins_response.json()["data"]
    checkins_payload = json.loads(
        _decrypt_response_export(client.get(checkins_data["download_url"]).content, checkins_data)
    )
    providers = {row["model_provider"] for row in checkins_payload["sections"]["model_runs"]}
    assert providers == {"external-provider", "fallback-provider"}
    serialized_checkins = json.dumps(checkins_payload, sort_keys=True)
    assert "Private knee note must stay hashed." not in serialized_checkins
    assert "Private fallback note must stay hashed." not in serialized_checkins
    for model_run in checkins_payload["sections"]["model_runs"]:
        assert "_redacted_fields" in model_run["safety_result"]

    assert health_response.status_code == 200
    health_data = health_response.json()["data"]
    health_payload = json.loads(
        _decrypt_response_export(client.get(health_data["download_url"]).content, health_data)
    )
    assert health_payload["sections"]["model_runs"] == []


def test_briefing_export_excludes_assistant_query_reasoning_traces(
    db_session: Session,
    tmp_path,
) -> None:
    user = _seed_user(db_session)
    checkin = _seed_checkin(db_session, user)
    ids = _seed_checkin_derived_artifacts(db_session, user, checkin)
    assistant_model_run = ModelRun(
        user_id=user.id,
        run_type=RunType.explanation,
        model_provider="assistant-provider",
        model_name="assistant-model",
        prompt_version="assistant-prompt-v1",
        input_hash="assistant-input-hash",
        output_hash="assistant-output-hash",
        schema_version="assistant-schema-v1",
        token_usage={},
        safety_result={"status": "passed"},
        input_metadata={
            "disclosure_payload": {"messages": [{"content": {"readiness_state": "low"}}]}
        },
    )
    db_session.add(assistant_model_run)
    db_session.flush()
    assistant_trace = _seed_assistant_trace(
        db_session,
        user,
        checkin.date,
        model_run_id=assistant_model_run.id,
    )
    export_store = LocalExportStore(tmp_path)
    client = _client(db_session, export_store)

    response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "briefings",
            "format": "json",
            "include_raw_data": False,
            "include_model_traces": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    payload = json.loads(_decrypt_response_export(client.get(data["download_url"]).content, data))
    sections = payload["sections"]
    assert set(sections) == {
        "daily_analysis_jobs",
        "readiness_assessments",
        "reasoning_traces",
        "model_runs",
    }
    exported_trace_ids = {UUID(row["id"]) for row in sections["reasoning_traces"]}
    assert exported_trace_ids == {ids["trace_id"]}
    assert assistant_trace.id not in exported_trace_ids
    assert {row["model_provider"] for row in sections["model_runs"]} == {
        "external-provider",
        "fallback-provider",
    }
    serialized = json.dumps(payload, sort_keys=True)
    assert "Can I run with my private knee concern?" not in serialized
    assert "Private assistant answer." not in serialized
    assert "assistant-provider" not in serialized


def test_per_entity_and_delete_all_remove_expected_records(db_session: Session, tmp_path) -> None:
    user = _seed_user(db_session)
    checkin = _seed_checkin(db_session, user)
    memory = _seed_memory(db_session, user)
    ids = _seed_checkin_derived_artifacts(db_session, user, checkin)
    _seed_raw_sample(db_session, user)
    export_store = LocalExportStore(tmp_path)
    client = _client(db_session, export_store)

    export_response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "checkins",
            "format": "json",
            "include_raw_data": False,
            "include_model_traces": False,
        },
    )
    export_data = export_response.json()["data"]
    export_job_id = UUID(export_data["export_job_id"])
    export_path = export_store.get(export_job_id).path
    assert export_path.exists()

    note_delete = client.delete(f"/v1/data/checkins/{checkin.id}/note")

    assert note_delete.status_code == 200
    db_session.refresh(checkin)
    assert checkin.free_text_note_reference is None
    assert checkin.free_text_note_summary is None
    note_audits = list(
        db_session.exec(
            select(AuditEvent).where(
                AuditEvent.user_id == user.id,
                AuditEvent.event_type == AuditEventType.data_deleted,
            )
        ).all()
    )
    assert note_audits[-1].event_metadata == {
        "target": "checkin_note",
        "checkin_id": str(checkin.id),
    }

    memory_delete = client.delete(f"/v1/data/memory-summaries/{memory.id}")
    assert memory_delete.status_code == 200
    assert db_session.get(MemorySummary, memory.id) is None
    memory_audits = list(
        db_session.exec(
            select(AuditEvent).where(
                AuditEvent.user_id == user.id,
                AuditEvent.event_type == AuditEventType.memory_deleted,
            )
        ).all()
    )
    assert memory_audits[-1].event_metadata == {
        "target": "memory_summary",
        "memory_summary_id": str(memory.id),
    }

    all_delete = client.delete("/v1/data/all")

    assert all_delete.status_code == 200
    deleted = all_delete.json()["data"]["deleted"]
    assert deleted["exports"] == 1
    assert deleted["daily_analysis_jobs"] == 1
    assert deleted["recommendations"] == 1
    assert deleted["readiness_assessments"] == 1
    assert deleted["reasoning_traces"] == 1
    assert deleted["model_runs"] == 2
    assert client.get(export_data["download_url"]).status_code == 404
    assert not export_path.exists()
    assert list(db_session.exec(select(DailyCheckIn)).all()) == []
    assert list(db_session.exec(select(RawHealthSample)).all()) == []
    assert db_session.get(ReasoningTrace, ids["trace_id"]) is None
    assert db_session.get(ModelRun, ids["model_run_id"]) is None
    assert db_session.get(ModelRun, ids["fallback_model_run_id"]) is None
    assert list(db_session.exec(select(ConsentRecord)).all()) == []
    assert list(db_session.exec(select(User)).all()) == []
    audits = list(db_session.exec(select(AuditEvent).order_by(col(AuditEvent.timestamp))).all())
    assert [audit.event_type for audit in audits] == [AuditEventType.data_deleted]
    assert audits[0].user_id is None
    assert audits[0].event_metadata["target"] == "all"
    assert audits[0].event_metadata["deleted"]["users"] == 1
    assert audits[0].event_metadata["deleted_user_hash"]


def test_delete_checkin_removes_derived_artifacts_with_copied_signals(
    db_session: Session,
) -> None:
    user = _seed_user(db_session)
    checkin = _seed_checkin(db_session, user)
    ids = _seed_checkin_derived_artifacts(db_session, user, checkin)
    assistant_trace = _seed_assistant_trace(db_session, user, checkin.date)
    client = _client(db_session)

    response = client.delete(f"/v1/data/checkins/{checkin.id}")

    assert response.status_code == 200
    deleted = response.json()["data"]["deleted"]
    assert deleted["daily_check_ins"] == 1
    assert deleted["daily_analysis_jobs"] == 1
    assert deleted["recommendations"] == 1
    assert deleted["readiness_assessments"] == 1
    assert deleted["reasoning_traces"] == 1
    assert deleted["model_runs"] == 2
    assert deleted["memory_summaries"] == 1
    assert db_session.get(DailyCheckIn, checkin.id) is None
    assert db_session.get(ReasoningTrace, ids["trace_id"]) is None
    assert db_session.get(ModelRun, ids["model_run_id"]) is None
    assert db_session.get(ModelRun, ids["fallback_model_run_id"]) is None
    assert db_session.get(MemorySummary, ids["memory_id"]) is None
    preserved_trace = db_session.get(ReasoningTrace, assistant_trace.id)
    assert preserved_trace is not None
    assert preserved_trace.trace_payload["assistant_queries"][0]["question"].startswith("Can I run")


def test_delete_checkin_note_removes_note_derived_memory_summary(db_session: Session) -> None:
    user = _seed_user(db_session)
    checkin = _seed_checkin(db_session, user)
    memory = MemorySummary(
        user_id=user.id,
        period_type=PeriodType.daily,
        start_date=checkin.date,
        end_date=checkin.date,
        summary_version="v1",
        observations=[
            {
                "kind": "observation",
                "source_refs": [
                    {
                        "table": "daily_check_in",
                        "id": str(checkin.id),
                        "field": "free_text_note",
                    }
                ],
            }
        ],
        hypotheses=[],
        source_refs=[{"table": "daily_check_in", "id": str(checkin.id), "field": "free_text_note"}],
    )
    db_session.add(memory)
    db_session.flush()
    client = _client(db_session)

    response = client.delete(f"/v1/data/checkins/{checkin.id}/note")

    assert response.status_code == 200
    deleted = response.json()["data"]["deleted"]
    assert deleted["checkin_notes"] == 1
    assert deleted["memory_summaries"] == 1
    db_session.refresh(checkin)
    assert checkin.free_text_note_reference is None
    assert checkin.free_text_note_summary is None
    assert db_session.get(MemorySummary, memory.id) is None


def test_record_consent_rejects_cloud_off_external_llm_enabled_state(
    db_session: Session,
) -> None:
    user = _seed_user(db_session, consent_version="v1")
    client = _client(db_session)

    response = client.post(
        "/v1/data/consent",
        json={
            "consent_version": "v2",
            "health_categories_enabled": ["all"],
            "cloud_processing_enabled": False,
            "external_llm_enabled": True,
            "raw_note_processing_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "consent_inconsistent"
    active = db_session.exec(
        select(ConsentRecord).where(
            ConsentRecord.user_id == user.id,
            col(ConsentRecord.revoked_at).is_(None),
        )
    ).one()
    assert active.consent_version == "v1"


def test_record_consent_rejects_privacy_mode_that_conflicts_with_flags(
    db_session: Session,
) -> None:
    user = _seed_user(db_session, consent_version="v1")
    client = _client(db_session)

    response = client.post(
        "/v1/data/consent",
        json={
            "consent_version": "v2",
            "health_categories_enabled": ["all"],
            "cloud_processing_enabled": False,
            "external_llm_enabled": False,
            "raw_note_processing_enabled": False,
            "privacy_mode": "cloud_assisted",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "consent_inconsistent"
    active = db_session.exec(
        select(ConsentRecord).where(
            ConsentRecord.user_id == user.id,
            col(ConsentRecord.revoked_at).is_(None),
        )
    ).one()
    assert active.consent_version == "v1"


def test_record_consent_bootstraps_first_user_and_health_sync(db_session: Session) -> None:
    client = _client(db_session)

    consent = client.post(
        "/v1/data/consent",
        json={
            "consent_version": "p1-04-v1",
            "health_categories_enabled": ["sleep"],
            "cloud_processing_enabled": True,
            "external_llm_enabled": False,
            "raw_note_processing_enabled": False,
            "privacy_mode": "hybrid",
        },
    )

    assert consent.status_code == 200
    users = list(db_session.exec(select(User)).all())
    assert len(users) == 1
    assert users[0].active_consent_version == "p1-04-v1"
    assert users[0].privacy_mode == PrivacyMode.hybrid
    assert consent.json()["data"]["consent_version"] == "p1-04-v1"

    sync = client.post(
        "/v1/health/sync",
        json={
            "client_sync_id": "bootstrap-sync",
            "device_id": "watch",
            "timezone": "UTC",
            "samples": [
                {
                    "source_sample_id": "sleep-bootstrap",
                    "sample_type": "sleep_duration",
                    "start_time": "2026-07-04T23:00:00Z",
                    "end_time": "2026-07-05T06:30:00Z",
                    "value": 7.5,
                    "unit": "h",
                }
            ],
            "consent_version": "p1-04-v1",
        },
    )

    assert sync.status_code == 200
    assert sync.json()["data"]["accepted_count"] == 1


def test_health_sync_rejects_stale_bootstrapped_consent(db_session: Session) -> None:
    client = _client(db_session)
    client.post(
        "/v1/data/consent",
        json={
            "consent_version": "v1",
            "health_categories_enabled": ["sleep"],
            "cloud_processing_enabled": True,
            "external_llm_enabled": False,
            "raw_note_processing_enabled": False,
        },
    )
    client.post(
        "/v1/data/consent",
        json={
            "consent_version": "v2",
            "health_categories_enabled": ["sleep"],
            "cloud_processing_enabled": True,
            "external_llm_enabled": False,
            "raw_note_processing_enabled": False,
        },
    )

    response = client.post(
        "/v1/health/sync",
        json={
            "client_sync_id": "stale-bootstrap-sync",
            "device_id": "watch",
            "timezone": "UTC",
            "samples": [
                {
                    "source_sample_id": "sleep-stale",
                    "sample_type": "sleep_duration",
                    "start_time": "2026-07-04T23:00:00Z",
                    "value": 7.5,
                    "unit": "h",
                }
            ],
            "consent_version": "v1",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_invalid"


def test_consent_endpoint_creates_exactly_one_user_and_active_record(db_session: Session) -> None:
    client = _client(db_session)

    response = client.post(
        "/v1/data/consent",
        json={
            "consent_version": "bootstrap-v1",
            "health_categories_enabled": ["all"],
            "cloud_processing_enabled": True,
            "external_llm_enabled": False,
            "raw_note_processing_enabled": False,
        },
    )

    assert response.status_code == 200
    users = list(db_session.exec(select(User)).all())
    assert len(users) == 1
    assert users[0].active_consent_version == "bootstrap-v1"
    records = list(db_session.exec(select(ConsentRecord)).all())
    assert len(records) == 1
    assert records[0].consent_version == "bootstrap-v1"
    assert records[0].revoked_at is None


def test_consent_endpoint_fails_closed_when_multiple_users_exist(db_session: Session) -> None:
    for consent_version in ("user-a-v1", "user-b-v1"):
        user = User(
            privacy_mode=PrivacyMode.hybrid,
            active_consent_version=consent_version,
        )
        db_session.add(user)
        db_session.flush()
        db_session.add(
            ConsentRecord(
                user_id=user.id,
                consent_version=consent_version,
                health_categories_enabled=["all"],
                cloud_processing_enabled=True,
                external_llm_enabled=False,
                raw_note_processing_enabled=False,
                timestamp=dt.datetime.now(dt.UTC),
            )
        )
        db_session.flush()
    client = _client(db_session)
    record_count_before = len(db_session.exec(select(ConsentRecord)).all())

    response = client.post(
        "/v1/data/consent",
        json={
            "consent_version": "new-v1",
            "health_categories_enabled": ["all"],
            "cloud_processing_enabled": True,
            "external_llm_enabled": False,
            "raw_note_processing_enabled": False,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ambiguous_user"
    assert len(db_session.exec(select(User)).all()) == 2
    assert len(db_session.exec(select(ConsentRecord)).all()) == record_count_before


def test_record_consent_rejects_unknown_health_category_without_persisting(
    db_session: Session,
) -> None:
    user = _seed_user(db_session, consent_version="v1")
    client = _client(db_session)

    response = client.post(
        "/v1/data/consent",
        json={
            "consent_version": "v2",
            "health_categories_enabled": ["private free-text category"],
            "cloud_processing_enabled": True,
            "external_llm_enabled": False,
            "raw_note_processing_enabled": False,
        },
    )

    assert response.status_code == 422
    records = list(
        db_session.exec(
            select(ConsentRecord)
            .where(ConsentRecord.user_id == user.id)
            .order_by(col(ConsentRecord.timestamp))
        ).all()
    )
    assert [record.consent_version for record in records] == ["v1"]
    assert records[0].health_categories_enabled == ["all"]


def test_consent_history_orders_records_and_records_category_revocation(
    db_session: Session,
) -> None:
    _seed_user(db_session, consent_version="v1", categories=["sleep", "activity"])
    client = _client(db_session)

    granted = client.post(
        "/v1/data/consent",
        json={
            "consent_version": "v2",
            "health_categories_enabled": ["sleep", "activity"],
            "cloud_processing_enabled": True,
            "external_llm_enabled": False,
            "raw_note_processing_enabled": False,
        },
    )
    revoked = client.post(
        "/v1/data/consent/revoke",
        json={
            "consent_version": "v3",
            "revoke_cloud_processing": False,
            "revoke_external_llm": False,
            "revoke_raw_note_processing": False,
            "revoke_health_categories": ["activity"],
        },
    )
    history = client.get("/v1/data/consent/history")

    assert granted.status_code == 200
    assert granted.json()["data"]["consent_version"] == "v2"
    assert revoked.status_code == 200
    assert revoked.json()["data"]["health_categories_enabled"] == ["sleep"]
    assert history.status_code == 200
    data = history.json()["data"]
    assert data["active_consent_version"] == "v3"
    assert [record["consent_version"] for record in data["records"]] == ["v3", "v2", "v1"]
    assert data["records"][0]["revoked_at"] is None
    assert data["records"][1]["revoked_at"] is not None
    assert data["records"][2]["revoked_at"] is not None


@pytest.mark.asyncio
async def test_cloud_revocation_cascades_and_blocks_checkin_and_llm(
    db_session: Session,
) -> None:
    user = _seed_user(
        db_session,
        consent_version="v1",
        external_llm_enabled=True,
        raw_note_processing_enabled=True,
    )
    client = _client(db_session)

    revoke = client.post(
        "/v1/data/consent/revoke",
        json={
            "consent_version": "v2-cloud-off",
            "revoke_cloud_processing": True,
            "revoke_external_llm": False,
            "revoke_raw_note_processing": False,
        },
    )

    assert revoke.status_code == 200
    consent = revoke.json()["data"]
    assert consent["cloud_processing_enabled"] is False
    assert consent["external_llm_enabled"] is False
    assert consent["raw_note_processing_enabled"] is False

    checkin = client.post(
        "/v1/checkins/daily",
        json={
            "date": "2026-07-04",
            "energy_score": 6,
            "sensitive_note_policy": "exclude_from_external_llm",
        },
    )
    assert checkin.status_code == 403
    assert checkin.json()["error"]["code"] == "cloud_processing_disabled"

    provider = FakeProvider()
    orchestrator = LLMOrchestrator(
        session=db_session,
        router=ModelRouter(
            providers=[provider],
            cheap_model="cheap",
            strong_model="strong",
        ),
    )
    result = await orchestrator.explain(user_id=user.id, prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "cloud_processing_disabled"
    assert provider.calls == []


def test_checkin_update_preserving_existing_note_requires_active_policy_consent(
    db_session: Session,
) -> None:
    user = _seed_user(
        db_session,
        consent_version="v1",
        external_llm_enabled=True,
        raw_note_processing_enabled=True,
    )
    checkin = _seed_checkin(db_session, user)
    client = _client(db_session)
    revoke = client.post(
        "/v1/data/consent/revoke",
        json={
            "consent_version": "v2-cloud-off",
            "revoke_cloud_processing": True,
            "revoke_external_llm": False,
            "revoke_raw_note_processing": False,
        },
    )
    assert revoke.status_code == 200

    update = client.put(
        f"/v1/checkins/daily/{checkin.id}",
        json={
            "date": checkin.date.isoformat(),
            "energy_score": 7,
            "sensitive_note_policy": "summarize_before_external_llm",
        },
    )

    assert update.status_code == 403
    assert update.json()["error"]["code"] == "cloud_processing_disabled"
    db_session.refresh(checkin)
    assert checkin.energy_score == 6
    assert checkin.free_text_note_reference == "summarize_before_external_llm:hash-only"


def test_consent_lifecycle_enforces_ingestion_checkin_and_llm(
    db_session: Session,
) -> None:
    user = _seed_user(
        db_session,
        categories=["sleep"],
        external_llm_enabled=True,
        raw_note_processing_enabled=True,
    )
    client = _client(db_session)

    sync_request = {
        "client_sync_id": "sync-consent",
        "device_id": "watch",
        "timezone": "UTC",
        "samples": [
            {
                "source_sample_id": "hrv-denied",
                "sample_type": "heart_rate_variability",
                "start_time": "2026-07-04T07:00:00Z",
                "value": 50.0,
                "unit": "ms",
            }
        ],
        "consent_version": "v1",
    }
    sync_response = client.post("/v1/health/sync", json=sync_request)
    assert sync_response.status_code == 403
    assert sync_response.json()["error"]["code"] == "consent_category_disabled"
    assert db_session.get(User, user.id) is not None

    disable = client.post(
        "/v1/data/consent/disable-external-llm",
        json={"consent_version": "v2"},
    )
    assert disable.status_code == 200
    assert disable.json()["data"]["external_llm_enabled"] is False

    checkin_response = client.post(
        "/v1/checkins/daily",
        json={
            "date": "2026-07-04",
            "free_text_note": "do not send",
            "sensitive_note_policy": "summarize_before_external_llm",
        },
    )
    assert checkin_response.status_code == 403
    assert checkin_response.json()["error"]["code"] == "external_llm_disabled"


def test_health_sync_rejects_cloud_processing_disabled_consent(db_session: Session) -> None:
    _seed_user(db_session, cloud_processing_enabled=False)
    client = _client(db_session)

    response = client.post(
        "/v1/health/sync",
        json={
            "client_sync_id": "sync-cloud-disabled",
            "device_id": "watch",
            "timezone": "UTC",
            "samples": [
                {
                    "source_sample_id": "hrv-cloud-disabled",
                    "sample_type": "heart_rate_variability",
                    "start_time": "2026-07-04T07:00:00Z",
                    "value": 50.0,
                    "unit": "ms",
                }
            ],
            "consent_version": "v1",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "cloud_processing_disabled"
    assert list(db_session.exec(select(RawHealthSample)).all()) == []


@pytest.mark.asyncio
async def test_external_llm_disable_routes_orchestrator_to_deterministic_local(
    db_session: Session,
) -> None:
    user = _seed_user(db_session, external_llm_enabled=True)
    client = _client(db_session)
    response = client.post(
        "/v1/data/consent/disable-external-llm",
        json={"consent_version": "v2"},
    )
    assert response.status_code == 200

    provider = FakeProvider()
    orchestrator = LLMOrchestrator(
        session=db_session,
        router=ModelRouter(
            providers=[provider],
            cheap_model="cheap",
            strong_model="strong",
        ),
    )

    result = await orchestrator.explain(user_id=user.id, prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "external_llm_disabled"
    assert provider.calls == []


def test_view_data_sent_returns_minimized_payload_without_raw_pii(db_session: Session) -> None:
    user = _seed_user(db_session)
    raw_pii = "private travel note and raw sample identifier"
    raw_key = "private model payload key"
    ModelRunLogger(db_session).log(
        user_id=user.id,
        run_type=RunType.explanation,
        provider="external-provider",
        model="model-a",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        input_payload={
            "provider": "external-provider",
            "model": "model-a",
            "prompt_version": "prompt-v1",
            "schema_version": "schema-v1",
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task_type": "simple_explanation",
                            "structured_feature_assessment": {
                                "readiness_state": "moderate",
                                "confidence": "medium",
                                raw_key: raw_pii,
                            },
                            "derived_features": {
                                "sleep_features": {"values": {"sleep_debt_hours": 0.3}}
                            },
                        }
                    ),
                }
            ],
        },
        output_payload={"summary": "ok"},
        token_usage={"total": 1},
        cost=None,
        latency_ms=1,
        safety_result={"status": "passed"},
    )
    client = _client(db_session)

    response = client.get("/v1/data/model-disclosures")

    assert response.status_code == 200
    body = response.text
    assert raw_pii not in body
    assert raw_key not in body
    data = response.json()["data"]
    assert data["runs"][0]["provider"] == "external-provider"
    payload_metadata = data["runs"][0]["payload_metadata"]
    assert payload_metadata["message_count"] == 1
    assert payload_metadata["messages"][0]["content_hash"]
    disclosure = payload_metadata["disclosure_payload"]["messages"][0]["content"]
    assert disclosure["structured_feature_assessment"]["readiness_state"] == "moderate"
    assert disclosure["derived_features"]["sleep_features"]["values"]["sleep_debt_hours"] == 0.3
    redacted_value = disclosure["structured_feature_assessment"]["_redacted_fields"][0]["value"]
    assert redacted_value["type"] == "string"
    assert redacted_value["character_count"] == len(raw_pii)
    assert redacted_value["hash"]
    assert data["runs"][0]["input_hash"]


def test_view_data_sent_sanitizes_legacy_model_metadata(db_session: Session) -> None:
    user = _seed_user(db_session)
    raw_pii = "raw legacy prompt that must not be disclosed"
    db_session.add(
        ModelRun(
            user_id=user.id,
            run_type=RunType.explanation,
            model_provider="external-provider",
            model_name="model-a",
            prompt_version="prompt-v1",
            input_hash="legacy-input-hash",
            output_hash="legacy-output-hash",
            schema_version="schema-v1",
            token_usage={},
            safety_result={"status": "passed"},
            input_metadata={
                "raw_prompt": raw_pii,
                "messages": [
                    {
                        "role": "user",
                        "content_hash": "content-hash",
                        "content_disclosure": {"secret_note": raw_pii},
                    }
                ],
                "disclosure_payload": {
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "task_type": "simple_explanation",
                                "secret_note": raw_pii,
                            },
                        }
                    ]
                },
            },
        )
    )
    db_session.flush()
    client = _client(db_session)

    response = client.get("/v1/data/model-disclosures")

    assert response.status_code == 200
    assert raw_pii not in response.text
    payload_metadata = response.json()["data"]["runs"][0]["payload_metadata"]
    assert "raw_prompt" not in payload_metadata
    assert "content_disclosure" not in payload_metadata["messages"][0]
    disclosure = payload_metadata["disclosure_payload"]["messages"][0]["content"]
    assert disclosure["task_type"] == "simple_explanation"
    assert disclosure["_redacted_fields"][0]["key_hash"]
    assert disclosure["_redacted_fields"][0]["value"]["hash"]


def test_llm_settings_returns_operator_config(db_session: Session) -> None:
    _seed_user(db_session)
    client = _client(db_session)

    response = client.get("/v1/data/llm-settings")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provider"] == "deepseek"
    assert data["cheap_model"] == "deepseek-v4-pro"
    assert data["strong_model"] == "deepseek-v4-pro"
    assert data["fallback_model"] == "baseline-local-deterministic-v1"
