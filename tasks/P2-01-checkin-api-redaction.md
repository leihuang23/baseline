# P2-01: Daily check-in API + redaction

**Phase:** 2 — Feature engine & check-in | **Depends on:** P0-04, P0-06, P0-02 | **Parallelizable with:** P2-02 | **Surface:** backend

## Context (self-contained)
Baseline combines objective HealthKit data with a **<1-minute subjective morning check-in**. Structured fields and free-text notes are stored separately, and **free-text is redacted/summarized locally before any external LLM call unless the user explicitly permits raw processing.** Sensitive (e.g. sexual-health) details are never required.

## Goal
Implement `POST /v1/checkins/daily` (+ edit/delete) with strict structured/free-text separation, a local redaction/summarization step for notes, and consent-aware handling of sensitive fields.

## Scope
In:
- Endpoint per §17.2: energy, mood, soreness, stress, perceived_recovery, food_quality scores; flags (alcohol, caffeine notes, illness, injury, travel); structured_notes; free_text_note; sensitive_note_policy → response (checkin_id, accepted_fields, redaction_status, analysis_job_id).
- Separate structured fields from free-text (FR-024); optional lifestyle/sexual-health indicators kept high-level and **optional**, never required (FR-023/025).
- Redaction/summarization of free-text before external processing; store a `free_text_note_reference` + policy, not raw text in places that flow to LLMs (FR-027, §20.4).
- Edit and delete a check-in (FR-026), with audit events.
- Validate under one minute of input is realistic (server accepts partial check-ins).
- Enqueue/attach to the daily-analysis job id.

Out:
- Feature computation (P2-02/03); the check-in UI (P2-05); the LLM redaction *summarizer* model call can be stubbed behind an interface here (real model wiring in P3-04).

## Deliverables
- `baseline_api/api/checkins.py` + check-in service + redaction interface.

## Acceptance criteria
- [ ] Structured vs free-text cleanly separated in storage; sensitive fields optional and defaulted off.
- [ ] Free-text is redacted/summarized per policy before it can reach an external LLM; raw note never logged.
- [ ] Edit/delete work and emit audit events.
- [ ] Partial check-ins accepted; validation errors are typed and clear.

## Tests required
- Redaction test: raw note never appears in the outbound-to-LLM payload or logs.
- Edit/delete tests; sensitive-field-optional test; partial-check-in acceptance test.

## PRD references
§17.2, FR-021–027, §20.4 LLM data controls, §12.3.
