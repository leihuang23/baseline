# P0-06: Observability, redaction & trace-ID foundation

**Phase:** 0 — Feasibility & foundations | **Depends on:** P0-01 | **Parallelizable with:** P0-03, P0-04 | **Surface:** backend

## Context (self-contained)
Baseline handles **restricted health data**. A hard rule: **no raw health samples, free-text notes, sexual-health notes, full personal prompts, or secrets may ever appear in logs.** Trace IDs must flow end to end (sync → features → reasoning → LLM → UI) so any recommendation is auditable. This foundation is used by every later slice.

## Goal
Provide structured logging with an enforced redaction layer, trace-ID propagation, and a metrics scaffold — before any pipeline code exists to (mis)log data.

## Scope
In:
- structlog config emitting JSON logs with: trace_id, job_id, internal/hashed user id, event_type, status, error_class, redacted metadata (§23.2).
- A **redaction processor** that scrubs/askarts known-sensitive keys and free-text by default; an allowlist model (only explicitly-safe fields pass). Default-deny for unknown large strings.
- Trace-ID middleware for FastAPI + a context propagation utility for background jobs; helper to attach the same trace_id through the daily pipeline.
- Prometheus metrics scaffold + registry and helper decorators (counters/histograms) for the §23.1 metric list (populated by later slices).
- A tiny logging API other modules call (so redaction can't be bypassed by using the raw logger).

Out:
- The dashboard UI (P5-03); actual metric emission from pipeline stages (each slice emits its own via these helpers).

## Deliverables
- `baseline_api/observability/` (logging, redaction, tracing, metrics) + usage doc.

## Acceptance criteria
- [ ] Attempting to log a raw sample / note / secret results in redacted output (verified by test), even if a developer passes it directly.
- [ ] trace_id is generated per request, returned in responses, and propagates into a job context.
- [ ] Metrics registry exposes a `/metrics` endpoint; helpers exist for each §23.1 metric.
- [ ] Default-deny: unknown free-text fields are redacted unless explicitly allowlisted.

## Tests required
- Redaction tests covering health sample, free-text note, token/secret, and a full "prompt with PII" string.
- Trace propagation test: request → job carries same trace_id.
- Property test (hypothesis): random dicts containing sensitive keys are always redacted.

## PRD references
§23 Observability, NFR-005, §20.3 (do not log secrets/health data), §22.2 privacy tests.
