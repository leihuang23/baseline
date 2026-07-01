# P6-02: Architecture & portfolio documentation

**Phase:** 6 — Portfolio packaging | **Depends on:** P6-01 (and all prior) | **Parallelizable with:** P6-01 | **Surface:** docs

## Context (self-contained)
The final portfolio artifact for Baseline: documentation that lets **a hiring manager understand the system, its architectural judgment, and its safety posture without any private data.** The headline stories are: SQL-for-personal-data vs RAG-for-external-knowledge, deterministic reasoning before LLM, safety-as-a-gate, evidence/confidence/uncertainty on every recommendation, and production practices (eval, observability, privacy).

## Goal
Write the architecture docs, README, evaluation report, failure-mode + safety-boundary docs, privacy/security notes, and a demo walkthrough — cohesive, accurate, and reviewer-oriented.

## Scope
In (§25 Phase 6 + §8.5):
- `README.md`: what Baseline is (and is not), the core pipeline diagram, **why SQL retrieval for personal data and RAG only for external knowledge** (§26.3/4), quickstart + demo instructions.
- `docs/architecture/`: system overview, deep modules (§16.3), data model, data flow, model routing, API contracts — reconciled with the shipped code.
- **Evaluation report**: what the harness covers (deterministic, LLM, retrieval, safety, privacy, regression), current pass rates, and the ≥30 golden scenarios (§8.5).
- Failure modes + degraded behavior (§16/§23), safety boundary (§19) and privacy/security notes (§20) — reader-facing summaries.
- Demo walkthrough doc pointing at the P6-01 scripted demo; screenshots/outputs from synthetic data only.
- Ensure docs contain no private data and stay consistent with code (add a docs-consistency checklist).

Out:
- New features; the demo implementation (P6-01).

## Deliverables
- `README.md` + `docs/**` (architecture, evaluation report, safety, privacy, demo walkthrough).

## Acceptance criteria
- [ ] A reviewer can understand the architecture + safety boundaries from docs alone, no private data present.
- [ ] README explains the SQL-vs-RAG decision and the deterministic-before-LLM principle.
- [ ] Evaluation report reflects actual harness results incl. ≥30 golden scenarios + safety evals.
- [ ] Failure modes, safety, and privacy documented; docs reconcile with shipped code.

## Tests required
- Docs-consistency check (links resolve, referenced endpoints/modules exist); leak check (no private data in docs).

## PRD references
§8.5 Portfolio metrics, §25 Phase 6, §26/§31 (principles), §16 architecture.
