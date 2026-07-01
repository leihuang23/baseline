# P5-02: External knowledge retrieval + citations

**Phase:** 5 — Knowledge retrieval & eval dashboard (V1) | **Depends on:** P5-01, P3-07 | **Parallelizable with:** P5-03 | **Surface:** backend

## Context (self-contained)
Retrieval-time half of Baseline's knowledge layer. External claims in user-facing answers **must be cited**, must stay **separated from personal evidence**, and general research must **never** be presented as personalized medical truth. Target ≥95% citation accuracy (§8.2). This plugs into the seam left in the assistant (P3-07) and briefing (P3-06, when `include_external_knowledge`).

## Goal
Implement vector retrieval over the curated corpus with citation binding, wire it into the assistant/briefing behind the opt-in flag, and add citation-accuracy + separation evals.

## Scope
In:
- Vector retrieval over the P5-01 corpus returning chunks + source metadata; relevance filtering.
- **Citation binding**: any external claim in an answer is tied to a retrieved source; unsupported external claims are not emitted (FR-072).
- Keep personal evidence and external sources in **separate** response fields end to end (FR-073); label general research as non-personalized (FR-074).
- Opt-in only: respect `include_external_knowledge` + consent; default off (NFR-007: briefing useful without RAG).
- Register **retrieval evals** (§22.2): correct-period/modality personal SQL retrieval remains separate; external corpus returns relevant sources; **citation accuracy ≥95%**; personal/external separation holds.

Out:
- Corpus ingestion (P5-01); the SQL personal retrieval itself (P3-07) — but assert separation here.

## Deliverables
- `baseline_api/retrieval/knowledge.py` + assistant/briefing integration + registered retrieval eval suite.

## Acceptance criteria
- [ ] External claims always carry a citation to a corpus source; uncited external claims are suppressed.
- [ ] Personal vs external evidence never mixed in responses; general research labeled non-personalized.
- [ ] Retrieval is opt-in; disabling it leaves the briefing/assistant fully functional.
- [ ] Citation-accuracy eval ≥95%; separation eval passes; suites gate CI.

## Tests required
- Citation-binding tests (claim ↔ source); no-uncited-claim test.
- Separation eval (personal vs external); citation-accuracy eval; opt-in/off functionality test.

## PRD references
§12.9 FR-070–076, §8.2 (≥95% citation accuracy), §22.2 retrieval evals, §26.3/4, NFR-007.
