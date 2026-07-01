# P0-03: Synthetic data fixtures & generators

**Phase:** 0 — Feasibility & foundations | **Depends on:** P0-02 | **Parallelizable with:** P0-04, P0-06 | **Surface:** backend (`packages/fixtures`)

## Context (self-contained)
Baseline must run entirely on **synthetic data** for tests, golden scenarios, and a public portfolio demo — real health data must never be required to develop or demo. This package is consumed by nearly every later slice (feature tests, reasoning scenarios, demo mode, eval harness).

## Goal
Build deterministic, parameterizable generators that produce realistic HealthKit-like samples, workouts, sleep, and check-ins across multi-week/month spans, plus a library of named scenario fixtures.

## Scope
In:
- Generators (seeded/deterministic) for: HRV, resting HR, sleep sessions (with stages), workouts (running + kettlebell/strength, with HR/distance/energy/modality), steps, VO2 max samples, and daily check-ins.
- Realistic dynamics: baselines with day-to-day noise, weekly training rhythm, sleep debt accumulation, illness/travel perturbations.
- Named scenario fixtures matching §22.2 golden scenarios (high HRV/good sleep/low load; low HRV/high RHR/poor sleep; mixed high-HRV+sleep-debt; 3 lower-body sessions in 6 days; illness flag + high motivation; missing HRV; stale sleep; VO2 improving + recovery declining; cognitive-priority week; medical-diagnosis request).
- A "60-day realistic persona" dataset for demo mode, containing zero real PII.
- Loader utilities to insert fixtures into a test DB and to emit raw-sync payloads (for exercising the sync API).

Out:
- Feature/reasoning logic (generators only produce inputs and, where useful, labeled *expected qualitative outcomes* for scenarios).

## Deliverables
- `packages/fixtures/` with generators, scenario catalog, and loaders.
- `docs/architecture/synthetic-data.md` describing scenarios and guarantees.

## Acceptance criteria
- [ ] Same seed → byte-identical output (deterministic).
- [ ] Each §22.2 scenario is available by name and loads into the DB.
- [ ] Generated data respects units/ranges the normalizer will expect.
- [ ] Contains no real personal data; suitable for a public repo.

## Tests required
- Determinism test (seed reproducibility).
- Sanity/range tests per metric; scenario-completeness test (all 10 named scenarios present).

## PRD references
§11.1 (≥30 synthetic scenarios), §22.2 golden scenarios, NFR-016, §26.14, FR-007.
