import json
from typing import Any

from baseline_api.observability.redaction import REDACTED, redact_log_event, redaction_processor


def _render(event: dict[str, Any]) -> str:
    return json.dumps(redact_log_event(event), sort_keys=True)


def test_raw_health_sample_is_redacted() -> None:
    output = _render(
        {
            "event_type": "sync.sample",
            "status": "failed",
            "metadata": {
                "samples": [
                    {
                        "metric_type": "heart_rate",
                        "value": 88,
                        "source_sample_id": "HKQuantitySample-private-id",
                    }
                ],
                "stage": "sync",
            },
        }
    )

    assert "HKQuantitySample-private-id" not in output
    assert "heart_rate" not in output
    assert REDACTED in output
    assert "sync" in output


def test_free_text_note_is_redacted() -> None:
    note = "Felt awful after drinking wine and sleeping badly."

    output = _render({"event_type": "checkin.note", "status": "ok", "metadata": {"note": note}})

    assert note not in output
    assert REDACTED in output


def test_free_text_event_name_is_redacted() -> None:
    note = "Felt awful after drinking wine and sleeping badly."

    output = _render({"event": note, "status": "ok", "metadata": {"stage": "redaction"}})

    assert note not in output
    assert REDACTED in output
    assert "redaction" in output


def test_free_text_event_type_is_redacted() -> None:
    note = "Felt awful after drinking wine and sleeping badly."

    output = _render({"event_type": note, "status": "ok", "metadata": {"stage": "redaction"}})

    assert note not in output
    assert REDACTED in output
    assert "redaction" in output


def test_structured_event_names_are_retained() -> None:
    output = _render(
        {
            "event": "sync.sample_rejected",
            "event_type": "sync.sample_rejected",
            "status": "blocked",
            "metadata": {"stage": "sync"},
        }
    )

    assert "sync.sample_rejected" in output
    assert "blocked" in output


def test_token_and_secret_are_redacted() -> None:
    output = _render(
        {
            "event_type": "llm.request",
            "status": "failed",
            "metadata": {
                "api_token": "sk-live-token-value",
                "client_secret": "secret-value",
            },
        }
    )

    assert "sk-live-token-value" not in output
    assert "secret-value" not in output
    assert output.count(REDACTED) == 2


def test_full_prompt_with_pii_is_redacted() -> None:
    prompt = "Write advice for Alice, alice@example.com, based on her HRV and sleep notes."

    output = _render(
        {
            "event_type": "llm.prompt",
            "status": "blocked",
            "metadata": {"prompt": prompt},
        }
    )

    assert prompt not in output
    assert "alice@example.com" not in output
    assert REDACTED in output


def test_unknown_free_text_metadata_is_default_denied() -> None:
    free_text = "This is an arbitrary developer-provided message that should not enter logs."

    output = _render(
        {
            "event_type": "developer.message",
            "status": "ok",
            "metadata": {"developer_message": free_text},
        }
    )

    assert free_text not in output
    assert REDACTED in output


def test_processor_redacts_direct_structlog_usage() -> None:
    event = redaction_processor(
        None,
        "info",
        {
            "event": "developer.raw",
            "samples": [{"value": 42, "source_sample_id": "raw-sample-id"}],
            "metadata": {"status_code": 200},
        },
    )

    rendered = json.dumps(event, sort_keys=True)
    assert "raw-sample-id" not in rendered
    assert event["samples"] == REDACTED
    assert event["metadata"]["status_code"] == 200
