# P1-03: Backfill & data-quality/completeness

**Phase:** 1 — Data ingestion MVP | **Depends on:** P1-02 | **Parallelizable with:** P1-04 | **Surface:** backend

## Context (self-contained)
Baseline must import historical Apple Health data and, crucially, be **honest about data quality** — stale or missing inputs must be visible so the user doesn't over-trust a briefing. Freshness and completeness are surfaced to the UI and feed the reasoning engine's confidence.

## Goal
Support historical backfill over large date ranges and compute per-day data-completeness + freshness signals that downstream slices (features, reasoning, UI) consume.

## Scope
In:
- Backfill job: chunked, resumable import of historical raw samples → normalization, without blocking daily sync; idempotent and safe to re-run (FR-016).
- Missing-expected-type detection per day (e.g., no HRV, no sleep) → structured completeness record + warnings (FR-012).
- Freshness/staleness computation: last-successful-sync per type; mark data stale past thresholds (FR-006 stale warnings, NFR data freshness).
- Expose completeness/freshness via a read model the daily-analysis and UI can query.
- Metrics: backfill duration, completeness by day, staleness flags (§23.1) via P0-06 helpers.

Out:
- The feature engine's use of these flags (P2-*); UI rendering (iOS slices).

## Deliverables
- `baseline_api/ingestion/backfill.py` + `.../data_quality.py` + read model/query.

## Acceptance criteria
- [ ] Backfilling a multi-month fixture completes, is resumable after interruption, and is idempotent.
- [ ] Days missing an expected type produce completeness warnings; present days do not.
- [ ] Stale data is flagged with the reason and age; fresh data is not.
- [ ] Completeness/freshness are queryable per user per day.

## Tests required
- Backfill resume/idempotency test on a large fixture.
- Completeness detection tests (missing HRV, missing sleep, complete day).
- Staleness threshold tests.

## PRD references
FR-012/016, §8.3 (freshness), §23.1 metrics, User stories 5/6/57.
