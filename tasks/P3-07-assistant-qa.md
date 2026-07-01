# P3-07: Assistant Q&A / follow-up

**Phase:** 3 — Reasoning, briefing, safety | **Depends on:** P3-06 | **Parallelizable with:** P3-08 | **Surface:** backend

## Context (self-contained)
Baseline lets the user interrogate their data ("Why not tempo today?", "How did recovery change this month?"). Answers must be **SQL/trace-backed over personal structured data** (not vector search over health data), must say "not enough data" when true, must keep **personal evidence separate from general research**, and must refuse medical diagnosis/treatment.

## Goal
Implement `POST /v1/assistant/query`: answer questions grounded in structured personal data + memory summaries, with citations to the trace/data, honest data-sufficiency disclosure, and safety enforcement.

## Scope
In:
- Endpoint per §17.5: question, date_context, allowed_data_scope, include_external_knowledge, privacy_mode → answer, personal_evidence[], external_sources[], confidence, uncertainty, safety_status, trace_id.
- Intent → **SQL/time-series retrieval** over personal data (recent history, compare-periods, modality-specific queries) (FR-078/080/082); reuse the same trace where a follow-up is about today's briefing (FR-060).
- "Not enough data" disclosure when the query can't be grounded (FR-079).
- Personal-data evidence kept **separate** from any external knowledge; external claims require citations (external retrieval itself is P5-02 — here provide the seam + separation) (FR-073).
- Support "what pattern did you learn about me?" (reads memory summaries) and "create a plan for this week" as a **candidate plan, not a prescription** (FR-083/084).
- Route through safety gate (P3-05): decline diagnosis/treatment, suggest professional consultation (FR-081).
- P95 < 15s for non-heavy queries (NFR-013).

Out:
- External-knowledge retrieval + citation implementation (P5-02) — stub the seam; memory generation (P4).

## Deliverables
- `baseline_api/api/assistant.py` + query-planning/retrieval service.

## Acceptance criteria
- [ ] Historical answers are SQL/trace-backed with personal_evidence populated; compare-periods works.
- [ ] "Not enough data" returned honestly when grounding is impossible.
- [ ] Personal vs external evidence separated; medical diagnosis/treatment queries refused/redirected via safety gate.
- [ ] "Plan for this week" returns a candidate plan framed as options, not a prescription.
- [ ] P95 < 15s for non-heavy queries.

## Tests required
- SQL-grounded answer tests (recent history, compare periods, modality); insufficient-data test.
- Safety-refusal test (diagnosis); personal/external separation test; plan-is-candidate test.

## PRD references
§17.5, §12.10 FR-077–084, §16.2, NFR-013, §26.3 (SQL for personal data).
