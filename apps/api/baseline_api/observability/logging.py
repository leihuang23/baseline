"""Structured logging API with enforced redaction."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any, Protocol, cast

import structlog

from baseline_api.observability.redaction import redaction_processor
from baseline_api.observability.tracing import get_trace_context

_configured = False


class BoundLogger(Protocol):
    def debug(self, event: str, **event_kw: Any) -> Any: ...

    def info(self, event: str, **event_kw: Any) -> Any: ...

    def warning(self, event: str, **event_kw: Any) -> Any: ...

    def error(self, event: str, **event_kw: Any) -> Any: ...


def configure_logging(log_level: str) -> None:
    """Configure JSON logs and the redaction processor."""

    global _configured
    logging.basicConfig(level=log_level)
    structlog.configure(
        processors=[
            _add_trace_context,
            redaction_processor,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, log_level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "baseline_api") -> BoundLogger:
    """Return the privacy-safe logger other modules should use."""

    if not _configured:
        configure_logging("INFO")
    return cast(BoundLogger, structlog.get_logger(name))


def log_event(
    event_type: str,
    *,
    status: str,
    metadata: dict[str, Any] | None = None,
    level: str = "info",
    job_id: str | None = None,
    user_id_hash: str | None = None,
    internal_user_id: str | None = None,
    error_class: str | None = None,
) -> None:
    """Emit one canonical redacted event."""

    context = get_trace_context()
    event = {
        "trace_id": context.trace_id,
        "job_id": job_id or context.job_id,
        "user_id_hash": user_id_hash or context.user_id_hash,
        "internal_user_id": internal_user_id or context.internal_user_id,
        "event_type": event_type,
        "status": status,
        "error_class": error_class,
        "metadata": metadata or {},
    }
    logger = get_logger()
    log_method = getattr(logger, level)
    log_method(event_type, **event)


def _add_trace_context(
    _: Any,
    __: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    context = get_trace_context()
    event_dict.setdefault("trace_id", context.trace_id)
    event_dict.setdefault("job_id", context.job_id)
    event_dict.setdefault("user_id_hash", context.user_id_hash)
    event_dict.setdefault("internal_user_id", context.internal_user_id)
    event_dict.setdefault("event_type", event_dict.get("event"))
    event_dict.setdefault("status", None)
    event_dict.setdefault("error_class", None)
    event_dict.setdefault("metadata", {})
    return event_dict
