# P0-04: API contracts & shared schemas

**Phase:** 0 — Feasibility & foundations | **Depends on:** P0-02 | **Parallelizable with:** P0-03, P0-06 | **Surface:** backend

## Context (self-contained)
Baseline's endpoints (§17) and its **recommendation output contract** (§18) are consumed by iOS, the dashboard, and the eval harness, so contracts are defined once, centrally, and versioned. Stack: FastAPI + Pydantic v2. Rule: **every user-facing recommendation carries evidence, confidence, uncertainty, and safety status.**

## Goal
Define all request/response Pydantic models for the §17 API surface and the §18 recommendation contract as the single source of truth, wire a consistent response envelope and error model, and publish OpenAPI.

## Scope
In:
- Pydantic v2 models for: `/v1/health/sync`, `/v1/checkins/daily`, `/v1/analysis/daily`, `/v1/briefings/{date}`, `/v1/assistant/query`, `/v1/recommendations/{id}/feedback`, `/v1/data/export` (requests + responses exactly per §17).
- The **recommendation output contract** (§18): readiness_state, recommendation_band, confidence, personal_evidence[], memory observations, external citations (optional), uncertainty[], data-quality notes, safety_note, alternatives, follow-up — with the §18 JSON example passing validation.
- Standard API envelope (status/data/error/meta), typed error model, and a `schema_version` on evolving payloads.
- FastAPI routers for these paths returning `501 Not Implemented` stubs validated against the schemas; OpenAPI served.

Out:
- Endpoint business logic (implemented in their phase slices).

## Deliverables
- `baseline_api/schemas/` (contracts) + `baseline_api/api/` (stub routers).
- Generated `docs/architecture/openapi.json` (or served at `/openapi.json`) + a short contract doc.

## Acceptance criteria
- [ ] All §17 payloads and the §18 contract are typed; enums for closed sets (bands, states, confidence).
- [ ] The §18 example JSON validates against the model; a rec missing evidence/confidence/uncertainty/safety **fails** validation.
- [ ] Envelope + error model consistent across all routes; OpenAPI generates cleanly.
- [ ] `schema_version` present where payloads will evolve.

## Tests required
- Round-trip (serialize/deserialize) tests for each contract.
- Negative tests: recommendation missing a mandatory field is rejected.
- Contract snapshot test to catch accidental breaking changes.

## PRD references
§17 API Contracts, §18 Recommendation Output Contract, §27.3 (contract tests), FR-055.
