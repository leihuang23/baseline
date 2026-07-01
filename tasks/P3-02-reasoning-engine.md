# P3-02: Reasoning engine (deterministic)

**Phase:** 3 — Reasoning, briefing, safety | **Depends on:** P2-03, P2-04, P3-01 | **Parallelizable with:** P3-04, P3-05 | **Surface:** backend

## Context (self-contained)
This is the **brain of Baseline — and it contains no LLM.** It turns deterministic features + goals + recent memory into a structured readiness assessment with a machine-readable trace. The LLM later only *explains* this output. Rule: **conservative defaults under uncertainty; the LLM must never override the risk flags produced here.**

## Goal
Implement a rules/confidence-based reasoning engine that consumes features, goals, recent memory, and constraints and emits a `ReadinessAssessment`: readiness state, evidence list, risk flags, recommendation band, confidence, uncertainty, follow-up questions, goal tradeoffs, and a reasoning trace.

## Scope
In:
- Consume derived features, active goals, recent memory summaries, and user constraints (FR-045).
- Produce: readiness_state, evidence[], risk_flags[], recommendation_band, confidence, uncertainty[], follow_up_questions[], goal_tradeoffs[], reasoning_trace_id (FR-046).
- Readiness explainable purely from rule outputs + feature values (FR-047).
- **Distinguish "low readiness because data is bad" vs "because physiology is unfavorable"** (FR-048) — a required, tested behavior.
- Detect conflicts (e.g. high motivation + poor recovery indicators) (FR-049); produce multiple candidate options when uncertainty is meaningful (FR-050).
- Conservative defaults when risk flags present (FR-051, §19.7); emit a machine-readable trace for every recommendation (FR-052).
- Encode confidence-reduction + conservative-recommendation triggers from the P0-05 confidence policy.
- Explicitly enforce: this engine can set **hard safety flags** the LLM cannot override (FR-053 boundary lives here + P3-05).

Out:
- LLM explanation (P3-04); post-generation safety gate (P3-05); briefing assembly (P3-06); the scenario suite (P3-03).

## Deliverables
- `baseline_api/reasoning/` engine + `ReadinessAssessment` persistence + trace emitter.

## Acceptance criteria
- [ ] Deterministic: same features/goals/memory → same assessment + trace.
- [ ] Every assessment includes evidence, risk flags, confidence, uncertainty, band, and a trace id.
- [ ] "Bad data" low-readiness is clearly distinguished from "unfavorable physiology" low-readiness.
- [ ] Conflicts detected; multiple options emitted under meaningful uncertainty; conservative default under risk flags.
- [ ] Goal tradeoffs computed from the active goal set.

## Tests required
- Property tests: mandatory fields always present; conservative default triggered by each risk flag.
- Bad-data-vs-physiology discrimination tests; conflict-detection tests; determinism test.

## PRD references
§12.6 FR-045–053, §16.3 Reasoning Engine, §19.7, §18 contract.
