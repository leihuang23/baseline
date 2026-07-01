# P1-02: Normalization module

**Phase:** 1 — Data ingestion MVP | **Depends on:** P1-01 | **Parallelizable with:** P1-04 | **Surface:** backend

## Context (self-contained)
Baseline converts messy raw HealthKit samples into **canonical, unit-normalized records** with provenance, so the deterministic feature engine can rely on clean inputs. This is a "deep module": a narrow interface (`normalize(raw) -> canonical`) hiding units, duplicates, and overlap handling. It must never fabricate data.

## Goal
Transform `RawHealthSample` batches into `NormalizedHealthMetric`, `WorkoutSession`, and `SleepSession` records — handling unit normalization, overlaps/conflicts, and workout/sleep classification — as an idempotent arq job.

## Scope
In:
- Unit normalization to canonical units per metric type (FR-017); reject/flag unknown units rather than guessing.
- Workout classification: modality, intensity, duration, distance, active energy, avg/max HR, intensity-zone distribution, source (FR-018).
- Sleep normalization across stages when available; handle interruptions and a quality proxy (FR-019).
- Conflict/overlap resolution for overlapping sleep/workout samples with a documented, deterministic policy (FR-020).
- Preserve `source_sample_ids` provenance and set `normalization_version`; confidence per record.
- Idempotent re-run: re-normalizing the same raw batch replaces/rebuilds canonical rows without duplication.

Out:
- Derived features (training load, sleep debt, baselines) — that is the feature engine (P2-02/03).
- Data-completeness warnings surface (P1-03).

## Deliverables
- `baseline_api/ingestion/normalization/` module + arq job triggered by P1-01.

## Acceptance criteria
- [ ] Deterministic: same raw input → same canonical output (golden fixtures).
- [ ] Units normalized; unknown units flagged, never silently coerced.
- [ ] Overlapping/conflicting samples resolved by the documented policy; provenance retained.
- [ ] `normalization_version` stamped; re-runs are idempotent.
- [ ] No fabricated values; gaps remain gaps.

## Tests required
- Golden conversion cases per metric/workout/sleep (from `packages/fixtures`).
- Overlap/conflict resolution cases; unit-normalization cases incl. unknown unit.
- Idempotent re-run test.

## PRD references
FR-017/018/019/020, §16.3 Normalization Module, §26.6, NFR-009.
