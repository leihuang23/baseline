"""Privacy helpers for model-run metadata and references."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from baseline_api.llm.hash import hash_payload
from baseline_api.llm.modelrun_logger import SAFE_DISCLOSURE_KEYS

SAFE_METADATA_KEYS = {
    "payload_hash",
    "payload_shape",
    "provider",
    "model",
    "prompt_version",
    "schema_version",
    "message_count",
    "messages",
    "disclosure_payload",
}
SAFE_MESSAGE_KEYS = {
    "role",
    "content_hash",
    "content_character_count",
    "content_shape",
}
SAFE_SHAPE_KEYS = {
    "type",
    "hash",
    "field_count",
    "fields",
    "key_hash",
    "value_shape",
    "count",
    "items",
}
SAFE_DISCLOSURE_STRING_FIELDS = {
    "schema_version",
    "task_type",
    "confidence",
    "readiness_state",
    "recommendation_band",
    "provider",
    "model",
    "prompt_version",
}
SAFE_DISCLOSURE_CONTAINER_KEYS = {"messages", "role", "content"}
SAFE_DISCLOSURE_DESCRIPTOR_KEYS = {"type", "character_count", "hash"}
SAFE_SAFETY_RESULT_KEYS = {
    "blocked",
    "categories",
    "category",
    "hard_safety_flags",
    "labels",
    "reason_codes",
    "risk_level",
    "severity",
    "status",
    "triggered_rules",
}
SAFE_ROLES = {"system", "user", "assistant", "tool", "unknown"}
SAFE_SHAPE_TYPES = {"object", "list", "null", "bool", "int", "float", "string"}
SAFE_PUBLIC_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/")


def model_run_ids_from_payload(value: Any) -> list[UUID]:
    """Extract model-run UUIDs from trace payloads without trusting raw shape."""

    ids: list[UUID] = []
    _collect_model_run_ids(value, ids)
    return _unique_ids(ids)


def sanitize_model_input_metadata(metadata: Any) -> dict[str, Any]:
    """Return model metadata safe for disclosure/export, including legacy rows."""

    if not isinstance(metadata, dict):
        return {}

    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if key_text not in SAFE_METADATA_KEYS:
            continue
        if key_text == "payload_hash" and isinstance(value, str):
            safe[key_text] = _safe_hash(value)
        elif key_text in {"provider", "model", "prompt_version", "schema_version"}:
            if isinstance(value, str):
                safe[key_text] = _safe_public_string(value)
        elif key_text == "message_count":
            if isinstance(value, int):
                safe[key_text] = value
        elif key_text == "messages":
            if isinstance(value, list):
                safe[key_text] = [_sanitize_message(item) for item in value]
        elif key_text == "disclosure_payload":
            safe[key_text] = _sanitize_disclosure_value(value)
        elif key_text == "payload_shape":
            safe[key_text] = _sanitize_shape(value)
    return safe


def sanitize_model_safety_result(safety_result: Any) -> dict[str, Any]:
    """Return model safety metadata safe for export without raw text side channels."""

    if not isinstance(safety_result, dict):
        return {}

    safe: dict[str, Any] = {}
    redacted_fields: list[dict[str, Any]] = []
    for key, value in sorted(safety_result.items(), key=lambda raw: str(raw[0])):
        key_text = str(key)
        if key_text in SAFE_SAFETY_RESULT_KEYS:
            safe[key_text] = _sanitize_safety_result_value(value, field_name=key_text)
        else:
            redacted_fields.append(
                {
                    "key_hash": hash_payload(key_text),
                    "value": _sanitize_disclosure_value(value),
                }
            )
    if redacted_fields:
        safe["_redacted_fields"] = redacted_fields
    return safe


def _collect_model_run_ids(value: Any, ids: list[UUID]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "model_run_id":
                _append_uuid(item, ids)
            elif key == "model_run_ids":
                if isinstance(item, list):
                    for candidate in item:
                        _append_uuid(candidate, ids)
                else:
                    _append_uuid(item, ids)
            _collect_model_run_ids(item, ids)
    elif isinstance(value, list):
        for item in value:
            _collect_model_run_ids(item, ids)


def _append_uuid(value: Any, ids: list[UUID]) -> None:
    if isinstance(value, UUID):
        ids.append(value)
    elif isinstance(value, str):
        try:
            ids.append(UUID(value))
        except ValueError:
            return


def _unique_ids(values: list[UUID]) -> list[UUID]:
    seen: set[UUID] = set()
    result: list[UUID] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _sanitize_message(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text not in SAFE_MESSAGE_KEYS:
            continue
        if key_text == "role" and isinstance(item, str):
            safe[key_text] = _safe_role(item)
        elif key_text == "content_hash" and isinstance(item, str):
            safe[key_text] = _safe_hash(item)
        elif key_text == "content_character_count" and isinstance(item, int):
            safe[key_text] = item
        elif key_text == "content_shape":
            safe[key_text] = _sanitize_shape(item)
    return safe


def _sanitize_shape(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text not in SAFE_SHAPE_KEYS:
                continue
            if key_text == "type" and isinstance(item, str):
                safe[key_text] = item if item in SAFE_SHAPE_TYPES else "unknown"
            elif key_text in {"hash", "key_hash"} and isinstance(item, str):
                safe[key_text] = _safe_hash(item)
            elif key_text in {"field_count", "count"} and isinstance(item, int):
                safe[key_text] = item
            elif key_text in {"fields", "items"} and isinstance(item, list):
                safe[key_text] = [_sanitize_shape(child) for child in item]
            elif key_text == "value_shape":
                safe[key_text] = _sanitize_shape(item)
        return safe
    if isinstance(value, list):
        return [_sanitize_shape(item) for item in value]
    return None


def _sanitize_safety_result_value(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        redacted_fields: list[dict[str, Any]] = []
        for key, item in sorted(value.items(), key=lambda raw: str(raw[0])):
            key_text = str(key)
            if key_text in SAFE_SAFETY_RESULT_KEYS:
                safe[key_text] = _sanitize_safety_result_value(item, field_name=key_text)
            else:
                redacted_fields.append(
                    {
                        "key_hash": hash_payload(key_text),
                        "value": _sanitize_disclosure_value(item),
                    }
                )
        if redacted_fields:
            safe["_redacted_fields"] = redacted_fields
        return safe
    if isinstance(value, list):
        return [_sanitize_safety_result_value(item, field_name=field_name) for item in value]
    if isinstance(value, str):
        return _safe_public_string(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return _sanitize_disclosure_value(value, field_name=field_name)


def _sanitize_disclosure_value(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        if _is_hashed_scalar_descriptor(value):
            return _sanitize_hashed_scalar_descriptor(value)

        items: dict[str, Any] = {}
        redacted_fields: list[dict[str, Any]] = []
        for key, item in sorted(value.items(), key=lambda raw: str(raw[0])):
            key_text = str(key)
            if key_text == "_redacted_fields" and isinstance(item, list):
                items[key_text] = [_sanitize_redacted_field(child) for child in item]
            elif key_text in SAFE_DISCLOSURE_KEYS or key_text in SAFE_DISCLOSURE_CONTAINER_KEYS:
                items[key_text] = _sanitize_disclosure_value(item, field_name=key_text)
            else:
                redacted_fields.append(
                    {
                        "key_hash": hash_payload(key_text),
                        "value": _sanitize_disclosure_value(item),
                    }
                )
        if redacted_fields:
            items["_redacted_fields"] = redacted_fields
        return items
    if isinstance(value, list):
        return [_sanitize_disclosure_value(item, field_name=field_name) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str) and field_name == "role":
        return _safe_role(value)
    if isinstance(value, str) and field_name in SAFE_DISCLOSURE_STRING_FIELDS:
        return _safe_public_string(value)
    return {"type": "string", "hash": hash_payload(value)}


def _is_hashed_scalar_descriptor(value: dict[Any, Any]) -> bool:
    return (
        "type" in value and "hash" in value and set(value).issubset(SAFE_DISCLOSURE_DESCRIPTOR_KEYS)
    )


def _sanitize_hashed_scalar_descriptor(value: dict[Any, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    value_type = value.get("type")
    if isinstance(value_type, str):
        safe["type"] = value_type if value_type in SAFE_SHAPE_TYPES else "unknown"
    value_hash = value.get("hash")
    if isinstance(value_hash, str):
        safe["hash"] = _safe_hash(value_hash)
    character_count = value.get("character_count")
    if isinstance(character_count, int) and character_count >= 0:
        safe["character_count"] = character_count
    return safe


def _sanitize_redacted_field(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"value": _sanitize_disclosure_value(value)}
    safe: dict[str, Any] = {}
    key_hash = value.get("key_hash")
    if isinstance(key_hash, str):
        safe["key_hash"] = _safe_hash(key_hash)
    safe["value"] = _sanitize_disclosure_value(value.get("value"))
    return safe


def _safe_hash(value: str) -> str:
    if len(value) == 64 and all(character in "0123456789abcdef" for character in value):
        return value
    return hash_payload(value)


def _safe_role(value: str) -> str | dict[str, str]:
    if value in SAFE_ROLES:
        return value
    return {"type": "string", "hash": hash_payload(value)}


def _safe_public_string(value: str) -> str | dict[str, str]:
    if 0 < len(value) <= 128 and all(character in SAFE_PUBLIC_CHARS for character in value):
        return value
    return {"type": "string", "hash": hash_payload(value)}
