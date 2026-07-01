# P1-01: Health sync API + idempotent ingestion

**Phase:** 1 — Data ingestion MVP | **Depends on:** P0-02, P0-04, P0-06 | **Parallelizable with:** — | **Surface:** backend

## Context (self-contained)
Baseline ingests Apple Health samples pushed from the iOS client. Raw samples are stored **separately from normalized data** with full provenance, and ingestion must be **idempotent** (duplicate rate < 0.1% per §8.3). Consent gates ingestion. Stack: FastAPI + SQLModel + arq/Redis. Trace IDs and redacted logging from P0-06 apply.

## Goal
Implement `POST /v1/health/sync` to accept incremental sample batches, dedupe idempotently, persist raw records with provenance and import-batch tracking, and return an anchor + data-quality summary.

## Scope
In:
- Endpoint per §17.1: request (client_sync_id, device_id, timezone, samples[], last_anchor, consent_version) → response (sync_id, accepted/duplicate/rejected counts, warnings[], next_anchor, data_quality_summary).
- Idempotency: dedupe by `source_sample_id` + content hash; safe to replay a batch (same result, no dupes). `client_sync_id` makes the whole call idempotent.
- Persist `RawHealthSample` with source_platform/device, sample_type, times, raw_value/unit, metadata, imported_at, import_batch_id.
- Consent check: reject/park ingestion if `consent_version` is missing/revoked for the category.
- Anchor bookkeeping so the client can resume from `next_anchor`.
- Enqueue a normalization job (arq) for accepted batches (job itself is P1-02).
- Structured, redacted logging + metrics (accepted/duplicate/rejected, latency) via P0-06 helpers.

Out:
- Normalization logic (P1-02); backfill orchestration (P1-03); HealthKit client (P1-04).

## Deliverables
- `baseline_api/ingestion/` sync service + router implementation + repository.

## Acceptance criteria
- [ ] Replaying an identical batch yields 0 new rows and reports them as duplicates.
- [ ] Partial batches with some known + some new samples are handled correctly.
- [ ] Provenance (source ids, import_batch_id) persisted; raw table only (no normalization here).
- [ ] Missing/invalid consent → request rejected with a typed error; nothing persisted.
- [ ] Response includes a usable `next_anchor` and a data-quality summary.

## Tests required
- Idempotency test (replay), mixed-batch test, malformed-sample rejection, consent-gate test.
- Integration test: sync → raw rows persisted + normalization job enqueued (job mocked).

## PRD references
§17.1, FR-008/009/010/011, §8.3 (duplicate < 0.1%), FR-005/006 (consent), §16.2 step 1.
