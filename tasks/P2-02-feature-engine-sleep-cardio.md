# P2-02: Feature engine — sleep & cardiovascular baselines

**Phase:** 2 — Feature engine & check-in | **Depends on:** P1-02 | **Parallelizable with:** P2-01 | **Surface:** backend

## Context (self-contained)
This is the **deterministic core** of Baseline. The feature engine turns canonical records into versioned, testable feature objects — the LLM never does this. Rule: **deterministic, reproducible, no fabricated measurements; gaps are reported, not filled.** Formulas are versioned (`feature_version`) so results are reproducible and auditable.

## Goal
Implement the sleep and cardiovascular portions of the daily feature engine: sleep duration/debt/consistency/quality proxy, and HRV + resting-HR baselines and deviations — as pure, deterministic functions writing `DerivedDailyFeature.sleep_features`/`hrv_features`/`rhr_features`.

## Scope
In:
- Sleep: daily duration, sleep debt (vs personal need), consistency (timing regularity), and a quality proxy (FR-034).
- HRV: rolling baseline + deviation from baseline; resting HR: baseline + deviation (FR-035). Baselines must handle the "not yet established" case explicitly.
- Data-quality/anomaly flags for stale/missing/anomalous/conflicting inputs (FR-043); recovery-confidence inputs derived from completeness (FR-040 partial — full recovery confidence combined in P2-03).
- Output structured feature objects with `feature_version` + calculation metadata (FR-041); pure functions, no I/O in the math (FR-042).
- Explicitly **do not fabricate** missing measurements (FR-044); emit `insufficient_data` markers instead.

Out:
- Training load / density / VO2 trend (P2-03); the comprehensive golden-fixture suite (P2-04); reasoning (P3-02).

## Deliverables
- `baseline_api/features/sleep.py`, `.../cardio.py`, shared `feature_types.py`, and a daily-feature assembler stub other feature slices extend.

## Acceptance criteria
- [ ] All functions are deterministic and side-effect-free; same inputs → identical outputs.
- [ ] "Baseline not established" and "missing input" are first-class outputs, never silently zero/guessed.
- [ ] Every feature object carries `feature_version` + calc metadata.
- [ ] Anomaly/stale/conflict flags produced for bad inputs.

## Tests required
- Golden unit tests for each formula (fixtures from `packages/fixtures`).
- Missing-baseline and missing-input path tests; anomaly-flag tests.
- Property test: no NaN/None leaks into a "computed" value; gaps stay gaps.

## PRD references
FR-034/035/040/041/042/043/044, §16.3 Feature Engine, §22.2 unit tests, NFR-009.
