# P2-04: Feature engine golden fixtures & determinism suite

**Phase:** 2 — Feature engine & check-in | **Depends on:** P2-02, P2-03, P0-07 | **Parallelizable with:** P2-05 | **Surface:** backend (`packages/eval` + tests)

## Context (self-contained)
Baseline's credibility rests on the feature engine being **provably deterministic and correct** — refactors must not silently change health reasoning. This slice hardens the engine with a comprehensive golden-fixture suite plugged into the eval harness (P0-07), so feature calculations are a portfolio-grade artifact.

## Goal
Build a thorough, versioned golden-fixture test suite for the entire feature engine and register it as a deterministic eval suite with CI enforcement.

## Scope
In:
- Golden fixtures (inputs + expected feature outputs) covering: normal days, missing HRV, missing sleep, stale data, anomalous spikes, conflicting samples, high-density training weeks, VO2 improving vs recovery declining.
- Exact-output assertions for deterministic features; explicit assertions that missing inputs yield `insufficient_data`/flags (never fabricated numbers).
- A `feature_version` change-detection test: if a formula changes, the suite forces an intentional fixture/version update (guards accidental drift).
- Register as a **deterministic eval suite** in `packages/eval`; wire into `make eval` + CI gate.
- Coverage ≥ 80% on `baseline_api/features/**` (target higher on formula code).

Out:
- Reasoning-engine scenarios (P3-03) — those consume features but test different behavior.

## Deliverables
- Fixtures + tests under `apps/api/tests/features/` and a registered eval suite in `packages/eval`.

## Acceptance criteria
- [ ] Every feature has at least one exact-output golden case + at least one missing/degraded-input case.
- [ ] Changing a formula without updating fixtures fails CI (drift guard).
- [ ] Suite runs via the eval harness and blocks CI on failure.
- [ ] Coverage target met on feature modules.

## Tests required
- The suite itself is the deliverable; include a meta-test that the suite is registered and discovered by the harness.

## PRD references
§22.2 unit tests + golden scenarios, §26.15, §27.2, FR-042, NFR-009.
