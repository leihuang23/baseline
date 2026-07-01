# P4-04: Data controls — export, delete, consent & "view data sent"

**Phase:** 4 — Memory & feedback (MVP-required) | **Depends on:** P0-02, P3-06, P3-04 | **Parallelizable with:** P4-01/02/03 | **Surface:** backend (+ minimal iOS entry)

## Context (self-contained)
Data control is an **MVP acceptance requirement** for Baseline (export & delete must work) and a trust cornerstone: the user owns their data, can see exactly what left the device to any model, and can revoke cloud/LLM processing. Consent is versioned and enforced across ingestion, check-in redaction, and the LLM orchestrator.

## Goal
Implement export, granular deletion, consent lifecycle + enforcement, and a "view data sent to external model" transparency feature.

## Scope
In:
- `POST /v1/data/export` (§17.7): export_scope, format, include_raw_data, include_model_traces → export_job_id, status, expires_at; encrypted export file with expiring link (§20.5) (FR-096).
- Deletion: delete all local+cloud data (FR-097); delete individual notes, check-ins, and memory summaries (FR-098); hard-delete verified by test; audit events.
- Consent: record/version consent (categories, cloud_processing, external_llm, raw_note_processing) with revocation (FR-005/006); **enforce** across ingestion (P1-01), check-in redaction (P2-01), and LLM orchestrator (P3-04).
- Disable external LLM processing at runtime (FR-099) → pipeline degrades to deterministic/local.
- "View data sent" (FR-100 / §20.4): reconstruct, from `ModelRun` metadata, exactly what (minimized/hashed) payload was sent to each provider — without persisting raw PII prompts.
- Minimal iOS settings entry points (export request, delete, disable-cloud, consent history, "view data sent"); full Settings UI can be a later slice.

Out:
- Full Trends/Memory/Settings UI (later); dashboard (P5-03).

## Deliverables
- `baseline_api/privacy/` (export, delete, consent, disclosure) + endpoints + minimal iOS entry.

## Acceptance criteria
- [ ] Export produces an encrypted, expiring file containing exactly the requested scope.
- [ ] Delete-all and per-entity delete remove the expected records (verified) + audit events.
- [ ] Disabling external LLM immediately routes the pipeline to deterministic/local; consent enforced everywhere.
- [ ] "View data sent" shows the minimized payload per provider with no raw PII.

## Tests required
- Export-contents test (scope correctness, encryption, expiry).
- Delete-removes-expected-records test (all + per-entity); external-LLM-disabled routing test.
- Consent-enforcement tests across ingestion/check-in/LLM; "view data sent" no-raw-PII test.

## PRD references
§17.7, §12.13 FR-096–100, §20.4/20.5, §22.2 privacy tests, §22.3 (export/delete required).
