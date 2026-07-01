# P4-02: Memory compiler — monthly & quarterly summaries

**Phase:** 4 — Memory & feedback (V1) | **Depends on:** P4-01 | **Parallelizable with:** P4-03 | **Surface:** backend

## Context (self-contained)
Extends Baseline's memory to medium/long horizons so the user can evaluate whether their lifestyle strategy is working over months/quarters (V1 scope). Same rules as P4-01: structured, versioned, observation-vs-hypothesis, confidence + source refs, sensitive exclusion.

## Goal
Implement monthly and quarterly summary generation from lower-horizon summaries, surfacing medium-term changes and durable learned patterns.

## Scope
In:
- Monthly summary from weekly/daily records; quarterly from monthly (FR-063).
- Emphasize medium-term trend deltas (e.g. VO2 trajectory, recovery arc, consistency) and durable "learned patterns about me" with confidence.
- Maintain observation/hypothesis separation, confidence, source_refs, sensitive exclusion, and auditable compaction (FR-064–069).
- Longitudinal correctness: quarterly numbers reconcile with the daily/weekly records they compact.
- Feed the "what pattern did you learn about me?" assistant query (P3-07) and Trends/Memory UI (later).

Out:
- Daily/weekly (P4-01); memory UI; forecasting (V2).

## Deliverables
- `baseline_api/memory/` monthly + quarterly compilers + accessors.

## Acceptance criteria
- [ ] Monthly + quarterly summaries generated and reconcilable with lower-horizon data.
- [ ] Learned patterns carry confidence + source refs; observation/hypothesis separated.
- [ ] Sensitive exclusion + auditable compaction preserved.

## Tests required
- Longitudinal fixture (≥90 days): monthly/quarterly reconciliation + pattern-extraction tests.
- Sensitive-exclusion + source-ref tests.

## PRD references
§11.2 (V1 memory), §12.8 FR-063–069, user stories 37–38, 34.
