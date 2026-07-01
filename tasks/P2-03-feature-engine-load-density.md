# P2-03: Feature engine — training load, density, VO2 trend & recovery confidence

**Phase:** 2 — Feature engine & check-in | **Depends on:** P2-02 | **Parallelizable with:** — | **Surface:** backend

## Context (self-contained)
Continues Baseline's **deterministic feature engine** (see P2-02). This slice adds training-load dynamics and the overall recovery-confidence score that the reasoning engine keys off. Same rules: versioned, deterministic, no fabrication, gaps explicit.

## Goal
Compute training load (acute + chronic windows), workout density by modality/muscle group, VO2 max trend, and an overall recovery-confidence score, writing `DerivedDailyFeature.training_load_features`/`recovery_features`/`goal_features`.

## Scope
In:
- Training load from available workout duration, intensity, HR, distance, modality (FR-036).
- Acute vs chronic load windows and their ratio/balance (FR-037).
- Workout density by muscle group / modality when classification is available (FR-038) — e.g. "3 lower-body sessions in 6 days".
- VO2 max trend when Apple Health provides VO2 samples; otherwise `insufficient_data` (FR-039).
- **Recovery confidence** combining input completeness + consistency (FR-040) — this is the confidence the reasoning engine consumes.
- Goal-relevant indicator hooks (goal_features) that the goal modules (P5) can extend; keep minimal here.
- Assemble the complete `DerivedDailyFeature` for a day (sleep+cardio from P2-02 + this slice), stamped with `feature_version`.

Out:
- Reasoning/readiness logic (P3-02); goal-specific module depth (Phase 5); golden-fixture suite consolidation (P2-04).

## Deliverables
- `baseline_api/features/training_load.py`, `.../recovery.py`, and the finalized daily-feature assembler + arq job that persists `DerivedDailyFeature`.

## Acceptance criteria
- [ ] Acute/chronic windows + density computed deterministically; "3 lower-body in 6 days" is detectable from output.
- [ ] VO2 trend present when samples exist, `insufficient_data` otherwise.
- [ ] Recovery confidence reflects completeness/consistency and drops when inputs are missing/stale.
- [ ] Full `DerivedDailyFeature` assembled + versioned + persisted; re-runs idempotent.

## Tests required
- Golden tests for load windows, density, VO2 trend, recovery confidence.
- Assembler integration test producing a complete daily feature object from a fixture day.

## PRD references
FR-036/037/038/039/040/041, §16.3, §22.2 unit tests.
