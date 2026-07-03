import pytest

from baseline_api.observability.redaction import REDACTED, redact_log_event

hypothesis = pytest.importorskip("hypothesis")
given = hypothesis.given
st = hypothesis.strategies

SENSITIVE_KEYS = [
    "raw_sample",
    "healthkit_payload",
    "free_text_note",
    "sexual_health_note",
    "api_token",
    "client_secret",
    "full_prompt",
]


@given(
    sensitive_key=st.sampled_from(SENSITIVE_KEYS),
    sensitive_value=st.text(min_size=1, max_size=100).filter(lambda value: value != REDACTED),
    extra=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(st.text(max_size=100), st.integers(), st.booleans(), st.none()),
        max_size=8,
    ),
)
def test_sensitive_keys_are_always_redacted(
    sensitive_key: str,
    sensitive_value: str,
    extra: dict[str, object],
) -> None:
    extra[sensitive_key] = sensitive_value
    event = redact_log_event(
        {"event_type": "property.redaction", "status": "ok", "metadata": extra}
    )

    assert event["metadata"][sensitive_key] == REDACTED
