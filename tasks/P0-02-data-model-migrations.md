# P0-02: Core data model & migrations

**Phase:** 0 — Feasibility & foundations | **Depends on:** P0-01 | **Parallelizable with:** P0-05 | **Surface:** backend

## Context (self-contained)
Baseline (personal physiological OS) must keep **strict separation between raw source data, normalized records, derived features, generated outputs, and evaluation traces** — this separation is a core portfolio talking point and an auditability requirement. Stack: PostgreSQL 16 + SQLModel (SQLAlchemy 2.0) + Alembic.

## Goal
Define all core entities from PRD §15 as SQLModel tables with Alembic migrations, enforcing the raw/normalized/derived/output/eval boundaries and full provenance.

## Scope
In — model every entity in §15 with the listed fields, typed and constrained:
- `User`, `ConsentRecord`, `RawHealthSample`, `NormalizedHealthMetric`, `WorkoutSession`, `SleepSession`, `DailyCheckIn`, `Goal`, `DerivedDailyFeature`, `ReadinessAssessment`, `Recommendation`, `MemorySummary`, `KnowledgeSource`, `ModelRun`, `EvaluationCase`, `AuditEvent`.
- Provenance: `source_sample_ids` links on normalized/derived rows; `import_batch_id` on raw.
- Versioning columns where PRD requires (`normalization_version`, `feature_version`, `assessment_version`, `summary_version`, prompt/schema versions on `ModelRun`).
- JSONB for structured sub-objects (e.g. `sleep_features`, `evidence_items`, `intensity_zone_distribution`); enums for `metric_type`, `modality`, `readiness_state`, `recommendation_band`, `period_type`, `privacy_mode`, `trust_level`.
- Indexes for time-series access patterns (`user_id`, `date`/`start_time`).
- Alembic initial migration + `make migrate` wiring; a repository/data-access layer stub per module folder.

Out:
- Business logic, ingestion, feature computation (later slices).
- Retrieval query implementations (P3-07 / P5-02).

## Deliverables
- SQLModel models organized by domain under `baseline_api/db/models/`.
- One Alembic migration that creates the full schema; `alembic upgrade head` succeeds on the compose Postgres.
- ER description in `docs/architecture/data-model.md`.

## Acceptance criteria
- [ ] Every §15 entity + field exists with sensible types, PK/FK, and enums (no free strings for closed sets).
- [ ] Raw / normalized / derived / output / eval tables are clearly separated; provenance FKs present.
- [ ] `alembic upgrade head` then `downgrade base` round-trips cleanly.
- [ ] Data-classification tag (restricted/confidential/internal per §20.2) documented per table.

## Tests required
- Migration up/down round-trip test against a throwaway Postgres.
- Model tests: insert+read each entity; provenance FK integrity; enum rejection of invalid values.

## PRD references
§15 Data Model (all entities), §20.2 Data Classification, §26.2/§26.11, NFR-009, NFR-017.
