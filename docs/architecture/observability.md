# Observability Foundation

Baseline logs are JSON structured events with a mandatory redaction processor.
Application code should use `baseline_api.observability.log_event()` or
`get_logger()` instead of configuring raw loggers.

Each log event carries:

- `trace_id`
- `job_id`
- `user_id_hash` or `internal_user_id`
- `event_type`
- `status`
- `error_class`
- redacted `metadata`

Redaction is default-deny for text. Known sensitive keys such as samples, notes,
prompts, tokens, secrets, raw payloads, and health or sexual-health fields are
always replaced with `[REDACTED]`. Unknown string metadata is also redacted
unless its key is explicitly allowlisted as operational metadata.

FastAPI requests receive an `X-Trace-Id` response header. Background jobs should
call `create_job_context(job_id=...)` inside request handling and pass the
result into queued work, then bind it with `use_trace_context(...)` while the job
runs.

The `/metrics` endpoint exposes the Prometheus registry. Application services
and background workers emit stage-specific values through the shared
observability helpers.
