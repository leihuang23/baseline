# P3-06: Daily briefing assembly + APIs

**Phase:** 3 — Reasoning, briefing, safety | **Depends on:** P3-02, P3-04, P3-05, P1-03 | **Parallelizable with:** — | **Surface:** backend

## Context (self-contained)
This slice wires Baseline's core pipeline end to end: **features → reasoning → (personal-history retrieval) → LLM explanation → safety gate → persist → serve.** The briefing is the product's daily heartbeat and every field of the §18 contract must be populated. A useful briefing must be producible **without external RAG** (NFR-007) and viewable offline (NFR-008).

## Goal
Implement the daily-analysis orchestration job and the briefing endpoints, assembling a complete, safety-validated, traceable daily briefing and persisting it with its trace.

## Scope
In:
- `POST /v1/analysis/daily` (§17.3): kick off the async pipeline (force_recompute, include_external_knowledge, privacy_mode) → job id + status.
- Pipeline job orchestrating: gather features + data-quality/freshness (P1-03) → reasoning assessment (P3-02) → personal-history retrieval (basic SQL recent-history; deep retrieval in P3-07/P5) → LLM explanation (P3-04) → safety gate (P3-05) → persist `Recommendation` + trace.
- `GET /v1/briefings/{date}` (§17.4) returning the full §18/§17.4 shape: readiness_state, confidence, data_freshness, evidence[], recommendation_band, candidate_options[], goal_tradeoffs[], uncertainty[], safety_notes[], trace_id, generated_at.
- Briefing includes FR-055 sections incl. "what would change my mind"; plain language, no medical certainty (FR-056/057); "show trace" support (FR-058).
- Degraded mode: if LLM/retrieval fails, still serve the deterministic assessment (NFR-007/011); store last briefing for offline view (NFR-008).
- Trace propagation (P0-06) through every stage; metrics: generation success, P95 latency (< 5 min, NFR-012), cost per briefing.

Out:
- Follow-up Q&A (P3-07); memory generation (P4); external knowledge (P5); UI (P3-08).

## Deliverables
- `baseline_api/briefing/` (orchestration job + endpoints) + trace persistence.

## Acceptance criteria
- [ ] End-to-end: a fixture day produces a persisted briefing with **every** §18 field populated + a trace id.
- [ ] Works with external knowledge disabled (NFR-007) and serves the last briefing offline.
- [ ] LLM/retrieval failure degrades to the deterministic assessment without corrupting data.
- [ ] Safety gate runs on every briefing; safety_notes present; no medical-certainty language.
- [ ] P95 generation < 5 min on the pipeline; cost + trace recorded.

## Tests required
- Integration test: fixture day → complete briefing (all fields) + trace.
- Degraded-mode test (LLM down → deterministic briefing); offline-last-briefing test; safety-note-present test.

## PRD references
§17.3/17.4, §12.7 FR-054–058, §16.2 data flow, NFR-007/008/011/012, §18 contract.
