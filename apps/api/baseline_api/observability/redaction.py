"""Default-deny redaction for logs and trace metadata."""

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
MAX_SAFE_STRING_LENGTH = 80
EVENT_NAME_KEYS = frozenset({"event", "event_type"})
SAFE_EVENT_NAME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)

SAFE_TOP_LEVEL_KEYS = frozenset(
    {
        "event",
        "timestamp",
        "level",
        "logger",
        "trace_id",
        "job_id",
        "user_id_hash",
        "internal_user_id",
        "event_type",
        "status",
        "error_class",
        "metadata",
    }
)

SAFE_METADATA_KEYS = frozenset(
    {
        "app_env",
        "attempt",
        "component",
        "count",
        "duration_ms",
        "duration_seconds",
        "endpoint",
        "error_code",
        "failure_count",
        "http_status",
        "job_type",
        "latency_ms",
        "method",
        "metric",
        "metric_name",
        "operation",
        "path",
        "prompt_hash",
        "reason",
        "redaction_status",
        "route",
        "schema_version",
        "source",
        "stage",
        "status",
        "status_code",
        "success_count",
        "trace_stage",
        "unit",
        "version",
    }
)

SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "address",
        "api_key",
        "authorization",
        "birth",
        "email",
        "health",
        "healthkit",
        "name",
        "note",
        "password",
        "payload",
        "phone",
        "prompt",
        "raw",
        "sample",
        "secret",
        "sexual",
        "ssn",
        "token",
    }
)

PII_VALUE_MARKERS = frozenset(
    {
        "@",
        "diagnosed",
        "doctor",
        "healthkit",
        "medication",
        "patient",
        "phone",
        "prompt",
        "sexual",
    }
)


def redact_log_event(event_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted log event shaped for structured logging."""

    redacted: dict[str, Any] = {}
    for key, value in event_dict.items():
        key_text = str(key)
        if key_text == "metadata" and isinstance(value, Mapping):
            redacted[key_text] = _redact_mapping(value, allowlist=SAFE_METADATA_KEYS)
        elif _is_sensitive_key(key_text):
            redacted[key_text] = REDACTED
        elif key_text in SAFE_TOP_LEVEL_KEYS:
            redacted[key_text] = _redact_value(value, key=key_text, allowlist=SAFE_TOP_LEVEL_KEYS)
        else:
            redacted[key_text] = _redact_value(value, key=key_text, allowlist=SAFE_METADATA_KEYS)

    metadata = redacted.get("metadata")
    if metadata is None:
        redacted["metadata"] = {}
    return redacted


def redaction_processor(
    _: Any,
    __: str,
    event_dict: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Structlog processor that enforces Baseline log redaction."""

    return redact_log_event(event_dict)


def _redact_mapping(value: Mapping[str, Any], *, allowlist: frozenset[str]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            redacted[key_text] = REDACTED
        else:
            redacted[key_text] = _redact_value(item, key=key_text, allowlist=allowlist)
    return redacted


def _redact_sequence(value: Sequence[Any], *, key: str, allowlist: frozenset[str]) -> list[Any]:
    return [_redact_value(item, key=key, allowlist=allowlist) for item in value]


def _redact_value(value: Any, *, key: str, allowlist: frozenset[str]) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _redact_string(value, key=key, allowlist=allowlist)
    if isinstance(value, Mapping):
        return _redact_mapping(value, allowlist=allowlist)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return _redact_sequence(value, key=key, allowlist=allowlist)
    return REDACTED


def _redact_string(value: str, *, key: str, allowlist: frozenset[str]) -> str:
    if _is_sensitive_key(key):
        return REDACTED
    if key in EVENT_NAME_KEYS:
        return value if _is_safe_event_name(value) else REDACTED
    if key not in allowlist:
        return REDACTED
    if len(value) > MAX_SAFE_STRING_LENGTH:
        return REDACTED
    if _looks_like_pii_or_prompt(value):
        return REDACTED
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _looks_like_pii_or_prompt(value: str) -> bool:
    normalized = value.lower()
    return any(marker in normalized for marker in PII_VALUE_MARKERS)


def _is_safe_event_name(value: str) -> bool:
    if not value or len(value) > MAX_SAFE_STRING_LENGTH:
        return False
    if value != value.strip() or "." not in value:
        return False
    return all(character in SAFE_EVENT_NAME_CHARACTERS for character in value)
