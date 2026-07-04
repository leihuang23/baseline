"""ModelRun telemetry writer."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlmodel import Session

from baseline_api.db.models.enums import RunType
from baseline_api.db.models.modelrun import ModelRun
from baseline_api.db.repositories.modelrun import ModelRunRepository
from baseline_api.llm.hash import hash_payload


class ModelRunLogger:
    """Persist redacted model execution telemetry."""

    def __init__(self, session: Session) -> None:
        self._runs = ModelRunRepository(session)

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
    ) -> ModelRun:
        return self._runs.create(
            ModelRun(
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
        )
