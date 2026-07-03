import json

from baseline_api.observability.logging import configure_logging, get_logger, log_event
from baseline_api.observability.tracing import TraceContext, use_trace_context


def test_log_event_uses_trace_context_and_redacts_metadata(capsys) -> None:
    with use_trace_context(TraceContext(trace_id="5cf33c4a-fb30-4958-89d4-98e9eb570cfc")):
        log_event(
            "privacy.test",
            status="blocked",
            metadata={"note": "raw private note", "stage": "redaction"},
        )

    rendered = capsys.readouterr().out

    assert "raw private note" not in rendered
    assert "5cf33c4a-fb30-4958-89d4-98e9eb570cfc" in rendered
    assert "redaction" in rendered


def test_logger_methods_emit_redacted_json(capsys) -> None:
    configure_logging("DEBUG")
    logger = get_logger("test")

    logger.debug("debug.event", metadata={"token": "private-token"})
    logger.warning("warning.event", metadata={"prompt": "Prompt with alice@example.com"})
    logger.error("error.event", metadata={"status_code": 500})

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert len(lines) >= 2
    assert all("private-token" not in json.dumps(line) for line in lines)
    assert all("alice@example.com" not in json.dumps(line) for line in lines)
    assert lines[-1]["metadata"]["status_code"] == 500
