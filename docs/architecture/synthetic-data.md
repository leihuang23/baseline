# Synthetic Data

Baseline uses synthetic data for development, tests, golden scenarios, and public demo mode. The fixtures in `packages/fixtures` are generated from deterministic seeds and contain no real personal health data, identifiers, free-text notes, or secrets.

## Guarantees

- Same generator config and seed produce byte-identical JSON via `fixture_to_json_bytes`.
- Samples use normalizer-ready metric names and units: HRV in `ms`, resting HR in `count/min`, steps in `count`, sleep duration in `h`, and VO2 max in `mL/kg/min`.
- Workouts are limited to the requested modalities: `run`, `kettlebell`, and `strength`.
- Sleep sessions include stage durations for `awake`, `core`, `deep`, and `rem`.
- Check-ins are structured only. Free-text note references are not generated.
- Scenario records may include qualitative expected outcomes, but they do not implement feature or reasoning logic.

## Scenario Catalog

The 10 PRD §22.2 golden scenarios are registered by name:

- `high_hrv_good_sleep_low_load`
- `low_hrv_high_rhr_poor_sleep`
- `mixed_high_hrv_sleep_debt`
- `three_lower_body_sessions_six_days`
- `illness_flag_high_motivation`
- `missing_hrv`
- `stale_sleep`
- `vo2_improving_recovery_declining`
- `cognitive_priority_week`
- `medical_diagnosis_request`

The catalog also includes a 60-day `demo_60_day_persona` and additional synthetic variants so the eval harness can grow toward the MVP requirement of at least 30 synthetic scenarios without using private data.

## Loading And Sync Payloads

`load_fixture(session, dataset)` inserts a fixture into the existing P0-02 tables:

- `user`
- `raw_health_sample`
- `normalized_health_metric`
- `workout_session`
- `sleep_session`
- `daily_check_in`

`emit_raw_sync_payload(dataset)` emits a HealthKit-like synthetic raw-sync payload for future API contract tests.

## Public Demo Boundary

The demo persona uses a synthetic device name, deterministic UUIDs, fixed dates, and structured synthetic notes only. Do not add real Apple Health exports, personal anecdotes, real locations, names, contact details, or raw free-text health notes to this package.
