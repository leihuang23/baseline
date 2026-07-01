# P3-03: Reasoning golden scenarios

**Phase:** 3 — Reasoning, briefing, safety | **Depends on:** P3-02, P0-07 | **Parallelizable with:** P3-04, P3-05 | **Surface:** backend (`packages/eval` + tests)

## Context (self-contained)
Baseline's MVP acceptance requires **≥30 golden scenarios pass** and that the system never hallucinates on missing data. This slice pins the reasoning engine's behavior to the §22.2 golden scenarios as property-based eval cases in the harness (P0-07).

## Goal
Encode the §22.2 golden scenarios (and enough variants to reach ≥30) as reasoning evals asserting structural properties, and wire them into `make eval` + CI.

## Scope
In:
- Scenario cases (from `packages/fixtures`) for: high HRV/good sleep/low load; low HRV/high RHR/poor sleep; mixed high-HRV + large sleep debt; three hard lower-body sessions in six days; illness flag + high motivation; missing HRV but complete sleep/workout; stale sleep data; VO2 improving but recovery declining; cognitive-work-priority week; user asks for medical diagnosis (routes to safety, not a training band).
- Variants to reach **≥30 total** scenarios.
- Property assertions (not brittle text): evidence present; confidence present; uncertainty present; band is conservative when risk flags fire; conflict detected where expected; missing-data → cautious/insufficient rather than fabricated certainty.
- Register as a reasoning eval suite; block CI on failure; results feed the dashboard shape.

Out:
- LLM-output evals (P3-04 mocks / P5), safety-refusal evals (P3-05) — cross-reference but keep suites separate per §27.5.

## Deliverables
- Scenario fixtures + `packages/eval` reasoning suite + tests.

## Acceptance criteria
- [ ] ≥30 scenarios registered and passing; the 10 named §22.2 scenarios all present.
- [ ] Each asserts structural properties, not exact prose.
- [ ] Missing-data scenarios prove no fabricated certainty.
- [ ] Suite gates CI.

## Tests required
- The suite is the deliverable; add a meta-test asserting ≥30 registered scenarios incl. the 10 named ones.

## PRD references
§22.2 golden scenarios, §22.3 (≥30 pass), §27.3, FR-048, §11.1.
