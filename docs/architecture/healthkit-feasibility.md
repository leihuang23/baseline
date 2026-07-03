# HealthKit Feasibility

P0 exit status: no unresolved HealthKit data-access blocker is known for P1.

This note fixes the Phase 0 boundary before ingestion work starts. It is not the
iOS implementation; P1-04 owns real-device permission, anchor, and resume tests.

## Categories For P1

P1 targets the minimum categories required by the PRD ingestion MVP:

| Baseline category | HealthKit shape | P1 use |
|-------------------|-----------------|--------|
| Sleep | Category samples with stage metadata when available | Sleep sessions and stale/missing sleep detection |
| Workouts | Workout samples | Modality, duration, distance, energy, and heart-rate summaries |
| Steps | Quantity samples | Daily activity volume |
| HRV | Quantity samples | Recovery baseline and acute deviation |
| Resting heart rate | Quantity samples | Recovery baseline and acute deviation |
| VO2 max | Quantity samples where available | Cardiorespiratory trend |

Optional later categories such as blood oxygen or body temperature stay out of
P1 unless explicitly enabled by a later slice.

## Sync Feasibility

- P1 will use anchored incremental reads and persist the last successful anchor.
- partial permissions are expected; missing categories must produce data-quality
  warnings rather than hard failures.
- Manual "Sync now" remains required because background refresh cannot be the
  only sync path.
- The backend contract for synthetic and real clients is
  `HealthSyncRequest`: one batch with `client_sync_id`, `device_id`,
  `timezone`, `last_anchor`, `consent_version`, and `samples[]`.

## P0 Decision

P0 can proceed to P1 with mocked/synthetic HealthKit-like payloads because the
required category list, permission-degradation rule, and anchored-sync contract
are explicit. P1-04 remains responsible for proving this on a real device and
recording any category availability gaps from the user's actual Apple Health
history.
