# P3-01: Goal management

**Phase:** 3 — Reasoning, briefing, safety | **Depends on:** P0-02, P0-04 | **Parallelizable with:** P3-04, P3-05 | **Surface:** backend

## Context (self-contained)
Baseline optimizes for the *user's actual priorities*, not generic fitness. Goals (with priority, horizon, constraints) feed the reasoning engine so it can **make goal tradeoffs explicit** — e.g. prioritizing sleep/cognitive work over a hard session. Goal conflict is a first-class product concept.

## Goal
Implement goal CRUD + lifecycle and a structured representation of goal priorities/constraints that the reasoning engine consumes to compute and explain tradeoffs.

## Scope
In:
- CRUD + pause/resume for `Goal` (category, priority, time_horizon, success_metric, constraints, active) (FR-028/030/032).
- Initial categories: cognitive performance, VO2 max, strength, recovery, sleep, long-term wellness (FR-029).
- A normalized "active goal set with priorities/constraints" accessor the reasoning engine can query (FR-031 support — conflict *detection* lives in P3-02, but the data shape must enable it).
- API endpoints for goal management; validation of priority ordering and constraints.
- Sexual-health/lifestyle goals kept high-level and optional, never clinical (FR-025 alignment).

Out:
- Conflict-resolution logic and tradeoff explanations (P3-02); goal-specific analytics modules (Phase 5); goal UI (P2-05).

## Deliverables
- `baseline_api/api/goals.py` + goal service + active-goal-set accessor.

## Acceptance criteria
- [ ] Full CRUD + pause/resume with all attributes; validation on priority/constraints.
- [ ] Active-goal-set accessor returns a clean structure (priorities, horizons, constraints) for the reasoning engine.
- [ ] Categories enumerated; invalid categories rejected.

## Tests required
- CRUD + pause/resume tests; accessor-shape test; validation tests.

## PRD references
§12.4 FR-028–033, §16.2, user stories 13–20.
