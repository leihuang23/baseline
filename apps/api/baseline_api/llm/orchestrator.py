"""LLM orchestration with schema validation, repair, fallback, and telemetry."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlmodel import Session, col, select

from baseline_api.db.models.enums import RunType
from baseline_api.db.models.user import ConsentRecord
from baseline_api.llm.modelrun_logger import ModelRunLogger
from baseline_api.llm.prompts import PromptRegistry
from baseline_api.llm.providers import ProviderError, ProviderRequest
from baseline_api.llm.router import ModelRouter
from baseline_api.llm.schemas import SCHEMA_VERSION, LLMExplanationOutput, PromptInputs
from baseline_api.llm.validation import (
    StructuredOutputError,
    degraded_output,
    parse_structured_output,
)
from baseline_api.safety.engine import SafetyPolicyEngine


class LLMConsentError(Exception):
    """Raised when an external model call is blocked by consent."""


class SafetyGate(Protocol):
    """Post-generation safety validation hook owned by P3-05."""

    def validate(
        self,
        output: LLMExplanationOutput,
        *,
        prompt_inputs: PromptInputs,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LLMConsent:
    """Minimal consent state needed before invoking an LLM provider."""

    external_llm_enabled: bool
    raw_note_processing_enabled: bool


class ConsentResolver(Protocol):
    """Resolve active user consent without coupling orchestration to storage."""

    def active_consent(self, user_id: UUID) -> LLMConsent: ...


class ModelRunTelemetryLogger(Protocol):
    """Persist or collect model run telemetry."""

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
    ) -> Any: ...


class DatabaseConsentResolver:
    """Load active consent from the database."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def active_consent(self, user_id: UUID) -> LLMConsent:
        statement = (
            select(ConsentRecord)
            .where(
                ConsentRecord.user_id == user_id,
                col(ConsentRecord.revoked_at).is_(None),
            )
            .order_by(col(ConsentRecord.timestamp).desc())
        )
        consent = self._session.exec(statement).first()
        if consent is None:
            raise LLMConsentError("Active consent record not found.")
        return LLMConsent(
            external_llm_enabled=consent.external_llm_enabled,
            raw_note_processing_enabled=consent.raw_note_processing_enabled,
        )


class PassThroughSafetyGate:
    """Explicit test helper for callers that intentionally bypass policy checks."""

    def validate(
        self,
        output: LLMExplanationOutput,
        *,
        prompt_inputs: PromptInputs,
    ) -> dict[str, Any]:
        return {
            "status": "passed",
            "gate": "pass_through",
            "schema_version": output.schema_version,
        }


@dataclass(frozen=True)
class FailClosedSafetyGate:
    """Safety gate used when the policy engine cannot be initialized."""

    reason: str
    error_type: str

    def validate(
        self,
        output: LLMExplanationOutput,
        *,
        prompt_inputs: PromptInputs,
    ) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": self.reason,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class OrchestratorResult:
    output: LLMExplanationOutput
    model_runs: list[Any] = field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None


class LLMOrchestrator:
    """Generate bounded explanations without letting LLMs compute or override facts."""

    def __init__(
        self,
        *,
        session: Session | None = None,
        router: ModelRouter,
        prompt_registry: PromptRegistry | None = None,
        safety_gate: SafetyGate | None = None,
        consent_resolver: ConsentResolver | None = None,
        model_run_logger: ModelRunTelemetryLogger | None = None,
        max_schema_attempts: int = 2,
    ) -> None:
        if max_schema_attempts < 1:
            raise ValueError("max_schema_attempts must be at least 1")
        if session is None and (consent_resolver is None or model_run_logger is None):
            raise ValueError(
                "session is required unless consent_resolver and model_run_logger are provided"
            )
        self._router = router
        self._prompts = prompt_registry or PromptRegistry()
        self._safety_gate = safety_gate or _default_safety_gate()
        self._consent_resolver = consent_resolver or DatabaseConsentResolver(
            _require_session(session)
        )
        self._logger = model_run_logger or ModelRunLogger(_require_session(session))
        self._max_schema_attempts = max_schema_attempts

    async def explain(
        self,
        *,
        user_id: UUID,
        prompt_inputs: PromptInputs,
        run_type: RunType = RunType.explanation,
    ) -> OrchestratorResult:
        consent = self._consent_resolver.active_consent(user_id)
        response_schema = LLMExplanationOutput.model_json_schema()
        prompt = self._prompts.render(prompt_inputs, response_schema)
        model_runs: list[Any] = []
        last_schema_error: str | None = None
        last_provider_error: str | None = None

        routes = self._router.routes_for(prompt_inputs.task_type)
        for route_index, route in enumerate(routes):
            provider = self._router.provider(route.provider_name)
            if provider.requires_external_llm_consent and not consent.external_llm_enabled:
                return self._degrade(
                    reason="external_llm_disabled",
                    prompt_inputs=prompt_inputs,
                    model_runs=model_runs,
                )
            if prompt_inputs.raw_notes and not consent.raw_note_processing_enabled:
                return self._degrade(
                    reason="raw_note_processing_disabled",
                    prompt_inputs=prompt_inputs,
                    model_runs=model_runs,
                )

            attempt_prompt = prompt
            for attempt_index in range(self._max_schema_attempts):
                request = ProviderRequest(
                    model=route.model,
                    messages=attempt_prompt.messages,
                    response_schema=response_schema,
                )
                input_payload = {
                    "provider": provider.name,
                    "model": route.model,
                    "prompt_version": attempt_prompt.version,
                    "messages": attempt_prompt.messages,
                    "schema_version": SCHEMA_VERSION,
                }
                started = time.perf_counter()
                try:
                    response = await provider.generate(request)
                except ProviderError as exc:
                    last_provider_error = "provider_error"
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    safety_result = {
                        "status": "provider_error",
                        "error_type": type(exc).__name__,
                    }
                    if route_index == len(routes) - 1:
                        safety_result["terminal_status"] = "degraded"
                    model_runs.append(
                        self._logger.log(
                            user_id=user_id,
                            run_type=run_type,
                            provider=provider.name,
                            model=route.model,
                            prompt_version=attempt_prompt.version,
                            schema_version=SCHEMA_VERSION,
                            input_payload=input_payload,
                            output_payload={"error": type(exc).__name__},
                            token_usage={},
                            cost=None,
                            latency_ms=latency_ms,
                            safety_result=safety_result,
                        )
                    )
                    break

                try:
                    output = parse_structured_output(response.content)
                except StructuredOutputError as exc:
                    last_schema_error = "schema_invalid"
                    safety_result = {"status": "schema_invalid"}
                    if (
                        route_index == len(routes) - 1
                        and attempt_index == self._max_schema_attempts - 1
                    ):
                        safety_result["terminal_status"] = "degraded"
                    model_runs.append(
                        self._logger.log(
                            user_id=user_id,
                            run_type=run_type,
                            provider=response.provider,
                            model=response.model,
                            prompt_version=attempt_prompt.version,
                            schema_version=SCHEMA_VERSION,
                            input_payload=input_payload,
                            output_payload=response.content,
                            token_usage=response.token_usage,
                            cost=response.cost,
                            latency_ms=response.latency_ms,
                            safety_result=safety_result,
                        )
                    )
                    attempt_prompt = self._prompts.repair(
                        original=prompt,
                        invalid_output=response.content,
                        validation_error=str(exc),
                    )
                    continue

                try:
                    safety_result = self._safety_gate.validate(output, prompt_inputs=prompt_inputs)
                except Exception as exc:
                    safety_result = {
                        "status": "blocked",
                        "reason": "safety_engine_error",
                        "error_type": type(exc).__name__,
                        "terminal_status": "degraded",
                    }
                    model_runs.append(
                        self._logger.log(
                            user_id=user_id,
                            run_type=run_type,
                            provider=response.provider,
                            model=response.model,
                            prompt_version=attempt_prompt.version,
                            schema_version=SCHEMA_VERSION,
                            input_payload=input_payload,
                            output_payload=response.content,
                            token_usage=response.token_usage,
                            cost=response.cost,
                            latency_ms=response.latency_ms,
                            safety_result=safety_result,
                        )
                    )
                    return self._degrade(
                        reason="safety_engine_error",
                        prompt_inputs=prompt_inputs,
                        model_runs=model_runs,
                    )
                safety_status = str(safety_result.get("status", "failed"))
                replacement_output: LLMExplanationOutput | None = None
                if safety_status in {"rewritten", "blocked", "escalated"}:
                    try:
                        replacement_output = LLMExplanationOutput.model_validate(
                            safety_result["safe_output"]
                        )
                    except (KeyError, TypeError, ValidationError):
                        safety_result = {
                            **safety_result,
                            "status": "blocked",
                            "terminal_status": "degraded",
                            "rewrite_error": "safe_output_invalid",
                        }
                        safety_status = "blocked"
                if replacement_output is not None:
                    output = replacement_output
                if safety_status not in {"passed", "rewritten"}:
                    safety_result = {**safety_result, "terminal_status": "degraded"}
                model_runs.append(
                    self._logger.log(
                        user_id=user_id,
                        run_type=run_type,
                        provider=response.provider,
                        model=response.model,
                        prompt_version=attempt_prompt.version,
                        schema_version=SCHEMA_VERSION,
                        input_payload=input_payload,
                        output_payload=response.content,
                        token_usage=response.token_usage,
                        cost=response.cost,
                        latency_ms=response.latency_ms,
                        safety_result=safety_result,
                    )
                )
                if safety_status not in {"passed", "rewritten"}:
                    if replacement_output is not None:
                        return OrchestratorResult(
                            output=output,
                            model_runs=model_runs,
                            degraded=True,
                            degrade_reason=f"safety_{safety_status}",
                        )
                    return self._degrade(
                        reason=f"safety_{safety_status}",
                        prompt_inputs=prompt_inputs,
                        model_runs=model_runs,
                    )
                return OrchestratorResult(output=output, model_runs=model_runs)

        reason = last_schema_error or last_provider_error or "llm_unavailable"
        return self._degrade(reason=reason, prompt_inputs=prompt_inputs, model_runs=model_runs)

    def _degrade(
        self,
        *,
        reason: str,
        prompt_inputs: PromptInputs,
        model_runs: list[Any],
    ) -> OrchestratorResult:
        return OrchestratorResult(
            output=degraded_output(
                deterministic_assessment=prompt_inputs.deterministic_assessment,
                reason=reason,
            ),
            model_runs=model_runs,
            degraded=True,
            degrade_reason=reason,
        )


def _require_session(session: Session | None) -> Session:
    if session is None:
        raise ValueError("session is required for database-backed LLM orchestration")
    return session


def _default_safety_gate() -> SafetyGate:
    try:
        return SafetyPolicyEngine.from_default_policy()
    except Exception as exc:
        return FailClosedSafetyGate(
            reason="safety_policy_load_error",
            error_type=type(exc).__name__,
        )
