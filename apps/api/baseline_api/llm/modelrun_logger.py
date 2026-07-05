"""ModelRun telemetry writer."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlmodel import Session

from baseline_api.db.models.enums import RunType
from baseline_api.db.models.modelrun import ModelRun
from baseline_api.db.repositories.modelrun import ModelRunRepository
from baseline_api.llm.hash import hash_payload

SAFE_DISCLOSURE_KEYS = {
    "candidate_options",
    "claim",
    "confidence",
    "data_quality",
    "data_quality_notes",
    "derived_features",
    "deterministic_assessment",
    "evidence_items",
    "external_citations",
    "external_knowledge",
    "follow_up_questions",
    "goal_tradeoffs",
    "hard_safety_flags",
    "hrv_features",
    "id",
    "model",
    "output_json_schema",
    "personal_evidence_refs",
    "provider",
    "readiness_state",
    "recovery_features",
    "recommendation_band",
    "retrieved_evidence",
    "retrieved_evidence_only",
    "risk_flags",
    "rhr_features",
    "schema_version",
    "sleep_debt_hours",
    "sleep_features",
    "structured_feature_assessment",
    "task_type",
    "training_load_features",
    "uncertainty",
    "values",
}


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
        input_metadata = minimized_payload_metadata(input_payload)
        input_metadata["run_type"] = run_type.value
        if not isinstance(input_metadata.get("feature"), str) or not input_metadata["feature"]:
            input_metadata["feature"] = run_type.value
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
                input_metadata=input_metadata,
            )
        )


def minimized_payload_metadata(input_payload: Any) -> dict[str, Any]:
    """Describe the outbound payload without persisting raw prompt content."""

    if not isinstance(input_payload, dict):
        return {
            "payload_hash": hash_payload(input_payload),
            "payload_shape": _summarize_value(input_payload),
        }

    messages = input_payload.get("messages")
    message_metadata = (
        [_message_metadata(message) for message in messages] if isinstance(messages, list) else []
    )
    return {
        "payload_hash": hash_payload(input_payload),
        "provider": input_payload.get("provider"),
        "model": input_payload.get("model"),
        "feature": input_payload.get("feature"),
        "task_type": input_payload.get("task_type"),
        "prompt_version": input_payload.get("prompt_version"),
        "schema_version": input_payload.get("schema_version"),
        "message_count": len(message_metadata),
        "messages": message_metadata,
        "disclosure_payload": {
            "provider": input_payload.get("provider"),
            "model": input_payload.get("model"),
            "prompt_version": input_payload.get("prompt_version"),
            "schema_version": input_payload.get("schema_version"),
            "messages": [
                _message_disclosure(message) for message in messages if isinstance(message, dict)
            ]
            if isinstance(messages, list)
            else [],
        },
    }


def _message_metadata(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {
            "role": "unknown",
            "content_hash": hash_payload(message),
            "content_character_count": 0,
            "content_shape": _summarize_value(message),
        }

    content = message.get("content", "")
    content_text = content if isinstance(content, str) else json.dumps(content, default=str)
    parsed_content = _json_content(content_text)
    content_shape = _summarize_value(parsed_content if parsed_content is not None else content)
    return {
        "role": _safe_message_role(message.get("role")),
        "content_hash": hash_payload(content_text),
        "content_character_count": len(content_text),
        "content_shape": content_shape,
        "content_disclosure": _disclose_value(
            parsed_content if parsed_content is not None else content,
        ),
    }


def _message_disclosure(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content", "")
    content_text = content if isinstance(content, str) else json.dumps(content, default=str)
    parsed_content = _json_content(content_text)
    return {
        "role": _safe_message_role(message.get("role")),
        "content": _disclose_value(parsed_content if parsed_content is not None else content),
    }


def _safe_message_role(role: Any) -> str:
    role_text = role if isinstance(role, str) else "unknown"
    return role_text if role_text in {"system", "user", "assistant", "tool"} else "unknown"


def _json_content(content: str) -> Any | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _summarize_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        sorted_items = sorted(value.items(), key=lambda item: str(item[0]))
        return {
            "type": "object",
            "field_count": len(sorted_items),
            "fields": [
                {
                    "key_hash": hash_payload(str(key)),
                    "value_shape": _summarize_value(item),
                }
                for key, item in sorted_items
            ],
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "count": len(value),
            "items": [_summarize_value(item) for item in value],
        }
    if value is None:
        return {"type": "null", "hash": hash_payload(None)}
    if isinstance(value, bool):
        value_type = "bool"
    elif isinstance(value, int):
        value_type = "int"
    elif isinstance(value, float):
        value_type = "float"
    else:
        value_type = "string"
    return {"type": value_type, "hash": hash_payload(value)}


def _disclose_value(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        items: dict[str, Any] = {}
        redacted_fields: list[dict[str, Any]] = []
        for key, item in sorted(value.items(), key=lambda raw: str(raw[0])):
            key_text = str(key)
            if key_text in SAFE_DISCLOSURE_KEYS:
                items[key_text] = _disclose_value(item, field_name=key_text)
            else:
                redacted_fields.append(
                    {
                        "key_hash": hash_payload(key_text),
                        "value": _disclose_value(item, field_name=None),
                    }
                )
        if redacted_fields:
            items["_redacted_fields"] = redacted_fields
        return items
    if isinstance(value, list):
        return [_disclose_value(item, field_name=field_name) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and field_name in {
        "schema_version",
        "task_type",
        "confidence",
        "readiness_state",
        "recommendation_band",
    }:
        return value
    return {
        "type": "string",
        "character_count": len(str(value)),
        "hash": hash_payload(value),
    }
