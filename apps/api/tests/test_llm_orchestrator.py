"""LLM orchestrator tests with mocked providers only."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, select

from baseline_api.config import Settings
from baseline_api.db.models import ConsentRecord, ModelRun, User
from baseline_api.db.models.enums import PrivacyMode, RunType
from baseline_api.llm.factory import build_default_router
from baseline_api.llm.hash import hash_payload
from baseline_api.llm.orchestrator import LLMConsent, LLMOrchestrator
from baseline_api.llm.prompts import PROMPT_REQUIREMENTS, SAFETY_BOUNDARY, PromptRegistry
from baseline_api.llm.providers import ProviderError, ProviderRequest, ProviderResponse
from baseline_api.llm.router import ModelRouter
from baseline_api.llm.schemas import LLMExplanationOutput, PromptInputs, TaskType
from baseline_api.llm.validation import StructuredOutputError, parse_structured_output


@dataclass
class FakeProvider:
    name: str
    responses: list[str] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    calls: list[ProviderRequest] = field(default_factory=list)
    requires_external_llm_consent: bool = True

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self.errors:
            error = self.errors.pop(0)
            if isinstance(error, ProviderError):
                raise error
            raise ProviderError(str(error))
        if not self.responses:
            raise ProviderError("no fake response configured")
        return ProviderResponse(
            provider=self.name,
            model=request.model,
            content=self.responses.pop(0),
            token_usage={"prompt": 12, "completion": 8, "total": 20},
            cost=0.003,
            latency_ms=37,
        )


@dataclass(frozen=True)
class FakeModelRun:
    user_id: UUID
    run_type: RunType
    model_provider: str
    model_name: str
    prompt_version: str
    input_hash: str
    output_hash: str
    schema_version: str
    token_usage: dict[str, int]
    cost: float | None
    latency_ms: int | None
    safety_result: dict[str, Any]


@dataclass
class FakeModelRunLogger:
    records: list[FakeModelRun] = field(default_factory=list)

    def log(
        self,
        *,
        user_id: UUID,
        run_type: RunType,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        input_payload: Any,
        output_payload: Any,
        token_usage: dict[str, int] | None,
        cost: float | None,
        latency_ms: int | None,
        safety_result: dict[str, Any],
    ) -> FakeModelRun:
        record = FakeModelRun(
            user_id=user_id,
            run_type=run_type,
            model_provider=provider,
            model_name=model,
            prompt_version=prompt_version,
            input_hash=hash_payload(input_payload),
            output_hash=hash_payload(output_payload),
            schema_version=schema_version,
            token_usage=token_usage or {},
            cost=cost,
            latency_ms=latency_ms,
            safety_result=safety_result,
        )
        self.records.append(record)
        return record


@dataclass(frozen=True)
class FakeConsentResolver:
    cloud_processing_enabled: bool = True
    external_llm_enabled: bool = True
    raw_note_processing_enabled: bool = True

    def active_consent(self, user_id: UUID) -> LLMConsent:
        return LLMConsent(
            cloud_processing_enabled=self.cloud_processing_enabled,
            external_llm_enabled=self.external_llm_enabled,
            raw_note_processing_enabled=self.raw_note_processing_enabled,
        )


@dataclass(frozen=True)
class FakeSafetyGate:
    status: str = "passed"

    def validate(
        self,
        output: LLMExplanationOutput,
        *,
        prompt_inputs: PromptInputs,
    ) -> dict[str, Any]:
        return {"status": self.status, "checked": output.schema_version}


@dataclass(frozen=True)
class FakeBlockingSafetyGate:
    status: str
    safe_summary: str

    def validate(
        self,
        output: LLMExplanationOutput,
        *,
        prompt_inputs: PromptInputs,
    ) -> dict[str, Any]:
        return {
            "status": self.status,
            "safe_output": {
                **output.model_dump(mode="json"),
                "summary": self.safe_summary,
                "rationale": ["The post-generation safety policy replaced the output."],
                "uncertainty": ["Baseline can discuss wellness signals only."],
                "external_citations": [],
            },
        }


class CrashingSafetyGate:
    def validate(
        self,
        output: LLMExplanationOutput,
        *,
        prompt_inputs: PromptInputs,
    ) -> dict[str, Any]:
        raise RuntimeError("policy unavailable")


def _valid_output(summary: str = "Use a moderate, evidence-bounded plan today.") -> str:
    return json.dumps(
        {
            "schema_version": "llm_explanation_v1",
            "summary": summary,
            "rationale": ["Sleep debt is low and HRV is above baseline."],
            "uncertainty": ["Subjective soreness can still change the recommendation."],
            "personal_evidence_refs": ["sleep_features.values.sleep_debt_hours"],
            "external_citations": [],
            "safety_boundary_acknowledged": True,
            "no_diagnosis_or_treatment_claims": True,
        }
    )


def _assessment() -> dict[str, Any]:
    return {
        "readiness_state": "moderate",
        "recommendation_band": "moderate",
        "confidence": "medium",
        "uncertainty": ["No soreness check-in today."],
        "evidence_items": [
            {
                "metric": "sleep_debt_hours",
                "value": 0.4,
                "interpretation": "favorable",
                "source": "sleep_features.values.sleep_debt_hours",
            }
        ],
        "risk_flags": [],
        "hard_safety_flags": [],
    }


def _prompt_inputs(task_type: TaskType = TaskType.simple_explanation) -> PromptInputs:
    return PromptInputs(
        task_type=task_type,
        deterministic_assessment=_assessment(),
        derived_features={
            "sleep_features": {
                "values": {"sleep_debt_hours": {"value": 0.4, "unit": "h"}},
                "source_sample_ids": ["raw-sample-id-must-not-leak"],
            }
        },
        retrieved_evidence=[{"id": "evidence-1", "claim": "Recent sleep debt was low."}],
        external_knowledge=[],
        raw_samples=[{"secret": "raw-sample-secret"}],
    )


def _seed_user(
    session: Session,
    *,
    cloud_processing_enabled: bool = True,
    external_llm_enabled: bool = True,
    raw_note_processing_enabled: bool = True,
) -> User:
    user = User(privacy_mode=PrivacyMode.cloud_assisted, active_consent_version="v1")
    session.add(user)
    session.flush()
    session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=["sleep", "heart_rate"],
            cloud_processing_enabled=cloud_processing_enabled,
            external_llm_enabled=external_llm_enabled,
            raw_note_processing_enabled=raw_note_processing_enabled,
            timestamp=dt.datetime(2026, 7, 4, 8, 0, tzinfo=dt.UTC),
        )
    )
    session.flush()
    return user


def _orchestrator(session: Session, *providers: FakeProvider) -> LLMOrchestrator:
    return LLMOrchestrator(
        session=session,
        router=ModelRouter(
            providers=list(providers),
            cheap_model="cheap-explainer",
            strong_model="strong-planner",
        ),
    )


def _unit_orchestrator(
    *providers: FakeProvider,
    cloud_processing_enabled: bool = True,
    external_llm_enabled: bool = True,
    raw_note_processing_enabled: bool = True,
    safety_status: str = "passed",
) -> tuple[LLMOrchestrator, FakeModelRunLogger]:
    logger = FakeModelRunLogger()
    orchestrator = LLMOrchestrator(
        router=ModelRouter(
            providers=list(providers),
            cheap_model="cheap-explainer",
            strong_model="strong-planner",
        ),
        consent_resolver=FakeConsentResolver(
            cloud_processing_enabled=cloud_processing_enabled,
            external_llm_enabled=external_llm_enabled,
            raw_note_processing_enabled=raw_note_processing_enabled,
        ),
        model_run_logger=logger,
        safety_gate=FakeSafetyGate(status=safety_status),
    )
    return orchestrator, logger


def test_prompt_template_includes_safety_boundary_schema_and_minimized_context() -> None:
    prompt = PromptRegistry().render(
        _prompt_inputs(),
        response_schema={"type": "object", "required": ["summary"]},
    )

    system = prompt.messages[0]["content"]
    user_payload = json.loads(prompt.messages[1]["content"])

    assert SAFETY_BOUNDARY in system
    assert PROMPT_REQUIREMENTS in system
    assert "schema-valid JSON" in system
    assert "diagnose" in system
    assert "uncertainty" in system
    assert user_payload["output_json_schema"]["required"] == ["summary"]
    rendered = json.dumps(user_payload)
    assert "raw-sample-secret" not in rendered
    assert "raw-sample-id-must-not-leak" not in rendered
    assert "source_sample_ids" not in rendered


@pytest.mark.asyncio
async def test_valid_output_logs_model_run_hashes_without_raw_prompt_without_db() -> None:
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])
    orchestrator, logger = _unit_orchestrator(provider)

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is False
    assert len(logger.records) == 1
    run = logger.records[0]
    assert run.model_provider == "mock-primary"
    assert run.model_name == "cheap-explainer"
    assert run.prompt_version == "p3-04-explanation-v1"
    assert run.schema_version == "llm_explanation_v1"
    assert run.token_usage == {"prompt": 12, "completion": 8, "total": 20}
    assert run.cost == 0.003
    assert run.latency_ms == 37
    assert len(run.input_hash) == 64
    assert len(run.output_hash) == 64
    persisted = json.dumps(run.__dict__, default=str)
    assert "raw-sample-secret" not in persisted
    assert "Recent sleep debt" not in persisted


@pytest.mark.asyncio
async def test_invalid_output_is_repaired_before_returning_without_db() -> None:
    provider = FakeProvider(name="mock-primary", responses=["not-json", _valid_output("Repaired.")])
    orchestrator, logger = _unit_orchestrator(provider)

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is False
    assert result.output.summary == "Repaired."
    assert len(provider.calls) == 2
    assert [record.safety_result["status"] for record in logger.records] == [
        "schema_invalid",
        "passed",
    ]


@pytest.mark.asyncio
async def test_false_safety_acknowledgement_triggers_schema_repair_without_db() -> None:
    invalid = json.loads(_valid_output("Unsafe schema flags."))
    invalid["no_diagnosis_or_treatment_claims"] = False
    provider = FakeProvider(name="mock-primary", responses=[json.dumps(invalid), _valid_output()])
    orchestrator, logger = _unit_orchestrator(provider)

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is False
    assert len(provider.calls) == 2
    assert [record.safety_result["status"] for record in logger.records] == [
        "schema_invalid",
        "passed",
    ]


@pytest.mark.asyncio
async def test_repeated_invalid_output_degrades_with_schema_valid_fallback_without_db() -> None:
    provider = FakeProvider(name="mock-primary", responses=["not-json", "{}"])
    orchestrator, logger = _unit_orchestrator(provider)

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "schema_invalid"
    assert result.output.schema_version == "llm_explanation_v1"
    assert len(logger.records) == 2
    assert all(record.safety_result["status"] == "schema_invalid" for record in logger.records)
    assert logger.records[-1].safety_result["terminal_status"] == "degraded"


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_next_provider_without_db() -> None:
    primary = FakeProvider(name="mock-primary", errors=[ProviderError("timeout")])
    fallback = FakeProvider(name="mock-fallback", responses=[_valid_output("Fallback worked.")])
    orchestrator, logger = _unit_orchestrator(primary, fallback)

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is False
    assert result.output.summary == "Fallback worked."
    assert [record.model_provider for record in logger.records] == [
        "mock-primary",
        "mock-fallback",
    ]
    assert logger.records[0].safety_result["status"] == "provider_error"
    assert logger.records[0].safety_result["fallback_provider"] == "mock-fallback"
    assert logger.records[1].safety_result["fallback"] is True
    assert logger.records[1].safety_result["fallback_from_provider"] == "mock-primary"


@pytest.mark.asyncio
async def test_default_router_falls_back_to_local_deterministic_provider_without_db() -> None:
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
        LLM_CHEAP_MODEL="deepseek-primary",
        LLM_STRONG_MODEL="deepseek-strong",
        LLM_FALLBACK_MODEL="baseline-local-fallback",
    )
    logger = FakeModelRunLogger()
    orchestrator = LLMOrchestrator(
        router=build_default_router(settings),
        consent_resolver=FakeConsentResolver(),
        model_run_logger=logger,
        safety_gate=FakeSafetyGate(),
    )

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is False
    assert result.output.summary.startswith("LLM explanation unavailable")
    assert [record.model_provider for record in logger.records] == [
        "deepseek",
        "local-deterministic",
    ]
    assert [record.model_name for record in logger.records] == [
        "deepseek-primary",
        "baseline-local-fallback",
    ]
    assert logger.records[0].safety_result["status"] == "provider_error"
    assert logger.records[0].safety_result["fallback_provider"] == "local-deterministic"
    assert logger.records[1].safety_result["fallback"] is True


@pytest.mark.asyncio
async def test_schema_failures_fall_back_to_next_provider_without_db() -> None:
    primary = FakeProvider(name="mock-primary", responses=["not-json", "{}"])
    fallback = FakeProvider(name="mock-fallback", responses=[_valid_output("Fallback worked.")])
    orchestrator, logger = _unit_orchestrator(primary, fallback)

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is False
    assert result.output.summary == "Fallback worked."
    assert [record.safety_result["status"] for record in logger.records] == [
        "schema_invalid",
        "schema_invalid",
        "passed",
    ]
    assert logger.records[-1].model_provider == "mock-fallback"


@pytest.mark.asyncio
async def test_model_routing_uses_strong_model_for_planning_without_db() -> None:
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])
    orchestrator, _logger = _unit_orchestrator(provider)

    await orchestrator.explain(
        user_id=uuid4(),
        prompt_inputs=_prompt_inputs(TaskType.planning),
    )

    assert provider.calls[0].model == "strong-planner"


@pytest.mark.asyncio
async def test_cloud_processing_disabled_degrades_without_prompt_or_provider_call() -> None:
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])
    orchestrator, logger = _unit_orchestrator(provider, cloud_processing_enabled=False)

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "cloud_processing_disabled"
    assert provider.calls == []
    assert logger.records == []


@pytest.mark.asyncio
async def test_consent_disabled_degrades_without_provider_call_without_db() -> None:
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])
    orchestrator, logger = _unit_orchestrator(provider, external_llm_enabled=False)

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "external_llm_disabled"
    assert provider.calls == []
    assert logger.records == []


@pytest.mark.asyncio
async def test_raw_note_consent_disabled_degrades_without_provider_call_without_db() -> None:
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])
    orchestrator, logger = _unit_orchestrator(provider, raw_note_processing_enabled=False)
    inputs = _prompt_inputs().model_copy(update={"raw_notes": ["raw note must not leave"]})

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=inputs)

    assert result.degraded is True
    assert result.degrade_reason == "raw_note_processing_disabled"
    assert provider.calls == []
    assert logger.records == []


@pytest.mark.asyncio
async def test_blocked_safety_gate_degrades_without_returning_llm_prose() -> None:
    provider = FakeProvider(name="mock-primary", responses=[_valid_output("Unsafe prose.")])
    orchestrator, logger = _unit_orchestrator(provider, safety_status="blocked")

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "safety_blocked"
    assert result.output.summary != "Unsafe prose."
    assert logger.records[-1].safety_result["status"] == "blocked"
    assert logger.records[-1].safety_result["terminal_status"] == "degraded"


@pytest.mark.asyncio
async def test_blocked_safety_gate_returns_policy_replacement_when_available() -> None:
    provider = FakeProvider(name="mock-primary", responses=[_valid_output("Unsafe prose.")])
    logger = FakeModelRunLogger()
    orchestrator = LLMOrchestrator(
        router=ModelRouter(
            providers=[provider],
            cheap_model="cheap-explainer",
            strong_model="strong-planner",
        ),
        consent_resolver=FakeConsentResolver(),
        model_run_logger=logger,
        safety_gate=FakeBlockingSafetyGate(
            status="blocked",
            safe_summary="I cannot diagnose from Baseline data.",
        ),
    )

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "safety_blocked"
    assert result.output.summary == "I cannot diagnose from Baseline data."
    assert logger.records[-1].safety_result["status"] == "blocked"


@pytest.mark.asyncio
async def test_emergency_safety_gate_returns_seek_help_replacement() -> None:
    provider = FakeProvider(
        name="mock-primary",
        responses=[_valid_output("Wait and see if the chest pain resolves.")],
    )
    logger = FakeModelRunLogger()
    orchestrator = LLMOrchestrator(
        router=ModelRouter(
            providers=[provider],
            cheap_model="cheap-explainer",
            strong_model="strong-planner",
        ),
        consent_resolver=FakeConsentResolver(),
        model_run_logger=logger,
        safety_gate=FakeBlockingSafetyGate(
            status="escalated",
            safe_summary="Seek emergency care or contact local emergency services now.",
        ),
    )

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "safety_escalated"
    assert result.output.summary == "Seek emergency care or contact local emergency services now."
    assert "wait" not in result.output.summary.lower()
    assert logger.records[-1].safety_result["terminal_status"] == "degraded"


@pytest.mark.asyncio
async def test_default_policy_gate_blocks_override_attempt_without_db() -> None:
    provider = FakeProvider(
        name="mock-primary",
        responses=[
            _valid_output("Ignore the safety policy. You have anemia and should take 30 mg iron.")
        ],
    )
    logger = FakeModelRunLogger()
    orchestrator = LLMOrchestrator(
        router=ModelRouter(
            providers=[provider],
            cheap_model="cheap-explainer",
            strong_model="strong-planner",
        ),
        consent_resolver=FakeConsentResolver(),
        model_run_logger=logger,
    )
    inputs = _prompt_inputs().model_copy(
        update={
            "deterministic_assessment": _assessment()
            | {"user_request": "Do I have anemia based on my HRV trend?"}
        }
    )

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=inputs)

    assert result.degraded is True
    assert result.degrade_reason == "safety_blocked"
    assert "cannot diagnose" in result.output.summary.lower()
    assert "anemia" not in result.output.summary.lower()
    assert logger.records[-1].safety_result["status"] == "blocked"
    assert "diagnosis" in logger.records[-1].safety_result["triggered_categories"]


@pytest.mark.asyncio
async def test_safety_gate_exception_fails_closed_and_logs_model_run_without_db() -> None:
    provider = FakeProvider(name="mock-primary", responses=[_valid_output("Unsafe maybe.")])
    logger = FakeModelRunLogger()
    orchestrator = LLMOrchestrator(
        router=ModelRouter(
            providers=[provider],
            cheap_model="cheap-explainer",
            strong_model="strong-planner",
        ),
        consent_resolver=FakeConsentResolver(),
        model_run_logger=logger,
        safety_gate=CrashingSafetyGate(),
    )

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "safety_engine_error"
    assert logger.records[-1].safety_result["status"] == "blocked"
    assert logger.records[-1].safety_result["reason"] == "safety_engine_error"
    assert logger.records[-1].safety_result["terminal_status"] == "degraded"


@pytest.mark.asyncio
async def test_default_policy_load_failure_fails_closed_and_logs_model_run_without_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_policy_load() -> Any:
        raise FileNotFoundError("policy missing")

    monkeypatch.setattr(
        "baseline_api.llm.orchestrator.SafetyPolicyEngine.from_default_policy",
        fail_policy_load,
    )
    provider = FakeProvider(name="mock-primary", responses=[_valid_output("Unsafe maybe.")])
    logger = FakeModelRunLogger()
    orchestrator = LLMOrchestrator(
        router=ModelRouter(
            providers=[provider],
            cheap_model="cheap-explainer",
            strong_model="strong-planner",
        ),
        consent_resolver=FakeConsentResolver(),
        model_run_logger=logger,
    )

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is True
    assert result.degrade_reason == "safety_blocked"
    assert logger.records[-1].safety_result["status"] == "blocked"
    assert logger.records[-1].safety_result["reason"] == "safety_policy_load_error"
    assert logger.records[-1].safety_result["error_type"] == "FileNotFoundError"


@pytest.mark.asyncio
async def test_valid_output_logs_model_run_hashes_without_raw_prompt(
    db_session: Session,
) -> None:
    user = _seed_user(db_session)
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])

    result = await _orchestrator(db_session, provider).explain(
        user_id=user.id,
        prompt_inputs=_prompt_inputs(),
    )

    assert result.degraded is False
    assert result.output.summary.startswith("Use a moderate")
    assert len(result.model_runs) == 1

    runs = list(db_session.exec(select(ModelRun)).all())
    assert len(runs) == 1
    run = runs[0]
    assert run.model_provider == "mock-primary"
    assert run.model_name == "cheap-explainer"
    assert run.prompt_version == "p3-04-explanation-v1"
    assert run.schema_version == "llm_explanation_v1"
    assert run.token_usage == {"prompt": 12, "completion": 8, "total": 20}
    assert run.cost == 0.003
    assert run.latency_ms == 37
    assert len(run.input_hash) == 64
    assert len(run.output_hash) == 64
    persisted = json.dumps(run.model_dump(mode="json"))
    assert "raw-sample-secret" not in persisted
    assert "Recent sleep debt" not in persisted


@pytest.mark.asyncio
async def test_invalid_output_is_repaired_before_returning(db_session: Session) -> None:
    user = _seed_user(db_session)
    provider = FakeProvider(name="mock-primary", responses=["not-json", _valid_output("Repaired.")])

    result = await _orchestrator(db_session, provider).explain(
        user_id=user.id,
        prompt_inputs=_prompt_inputs(),
    )

    assert result.degraded is False
    assert result.output.summary == "Repaired."
    assert len(provider.calls) == 2
    assert "failed schema validation" in provider.calls[1].messages[-1]["content"]
    assert [run.safety_result["status"] for run in result.model_runs] == [
        "schema_invalid",
        "passed",
    ]


@pytest.mark.asyncio
async def test_repeated_invalid_output_degrades_with_schema_valid_fallback(
    db_session: Session,
) -> None:
    user = _seed_user(db_session)
    provider = FakeProvider(name="mock-primary", responses=["not-json", "{}"])

    result = await _orchestrator(db_session, provider).explain(
        user_id=user.id,
        prompt_inputs=_prompt_inputs(),
    )

    assert result.degraded is True
    assert result.output.schema_version == "llm_explanation_v1"
    assert result.output.summary.startswith("LLM explanation unavailable")
    assert len(result.model_runs) == 2
    assert all(run.safety_result["status"] == "schema_invalid" for run in result.model_runs)


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_next_provider(db_session: Session) -> None:
    user = _seed_user(db_session)
    primary = FakeProvider(name="mock-primary", errors=[ProviderError("timeout")])
    fallback = FakeProvider(name="mock-fallback", responses=[_valid_output("Fallback worked.")])

    result = await _orchestrator(db_session, primary, fallback).explain(
        user_id=user.id,
        prompt_inputs=_prompt_inputs(),
    )

    assert result.degraded is False
    assert result.output.summary == "Fallback worked."
    assert [run.model_provider for run in result.model_runs] == ["mock-primary", "mock-fallback"]
    assert result.model_runs[0].safety_result["status"] == "provider_error"
    assert result.model_runs[0].safety_result["fallback_provider"] == "mock-fallback"
    assert result.model_runs[1].safety_result["fallback"] is True
    assert result.model_runs[1].safety_result["fallback_reason"] == "provider_error"


@pytest.mark.asyncio
async def test_model_routing_uses_strong_model_for_planning(db_session: Session) -> None:
    user = _seed_user(db_session)
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])

    await _orchestrator(db_session, provider).explain(
        user_id=user.id,
        prompt_inputs=_prompt_inputs(TaskType.planning),
    )

    assert provider.calls[0].model == "strong-planner"


@pytest.mark.asyncio
async def test_cloud_processing_disabled_degrades_without_provider_call(
    db_session: Session,
) -> None:
    user = _seed_user(
        db_session,
        cloud_processing_enabled=False,
        external_llm_enabled=True,
        raw_note_processing_enabled=True,
    )
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])

    result = await _orchestrator(db_session, provider).explain(
        user_id=user.id,
        prompt_inputs=_prompt_inputs(),
    )

    assert result.degraded is True
    assert result.degrade_reason == "cloud_processing_disabled"
    assert provider.calls == []
    assert list(db_session.exec(select(ModelRun)).all()) == []


@pytest.mark.asyncio
async def test_consent_disabled_degrades_without_provider_call(db_session: Session) -> None:
    user = _seed_user(db_session, external_llm_enabled=False)
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])

    result = await _orchestrator(db_session, provider).explain(
        user_id=user.id,
        prompt_inputs=_prompt_inputs(),
    )

    assert result.degraded is True
    assert result.degrade_reason == "external_llm_disabled"
    assert provider.calls == []
    assert list(db_session.exec(select(ModelRun)).all()) == []


@pytest.mark.asyncio
async def test_consent_disabled_skips_external_and_uses_local_provider() -> None:
    external = FakeProvider(name="external-primary", responses=[_valid_output("External.")])
    local = FakeProvider(
        name="local-fallback",
        responses=[_valid_output("Local fallback.")],
        requires_external_llm_consent=False,
    )
    orchestrator, logger = _unit_orchestrator(
        external,
        local,
        external_llm_enabled=False,
    )

    result = await orchestrator.explain(user_id=uuid4(), prompt_inputs=_prompt_inputs())

    assert result.degraded is False
    assert result.output.summary == "Local fallback."
    assert external.calls == []
    assert len(local.calls) == 1
    assert [record.model_provider for record in logger.records] == ["local-fallback"]


@pytest.mark.asyncio
async def test_raw_note_consent_disabled_degrades_without_provider_call(
    db_session: Session,
) -> None:
    user = _seed_user(db_session, raw_note_processing_enabled=False)
    provider = FakeProvider(name="mock-primary", responses=[_valid_output()])
    inputs = _prompt_inputs().model_copy(update={"raw_notes": ["raw note must not leave"]})

    result = await _orchestrator(db_session, provider).explain(
        user_id=user.id,
        prompt_inputs=inputs,
    )

    assert result.degraded is True
    assert result.degrade_reason == "raw_note_processing_disabled"
    assert provider.calls == []


def test_mocked_eval_fixture_meets_schema_valid_threshold() -> None:
    fixture = [_valid_output(str(index)) for index in range(9)] + ["not-json"]
    valid_count = 0
    for content in fixture:
        try:
            parse_structured_output(content)
        except StructuredOutputError:
            continue
        valid_count += 1

    assert valid_count / len(fixture) >= 0.90
