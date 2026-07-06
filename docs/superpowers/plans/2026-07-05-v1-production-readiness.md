# V1 Production Readiness Implementation Plan

> **Execution approach:** This plan is implemented one module at a time in an isolated feature branch/worktree. Each module is built by a fresh implementer subagent, reviewed for spec compliance, reviewed for code quality, fixed if needed, and then committed before the next module begins.

**Goal:** Close the remaining gaps between the current portfolio-grade MVP and the PRD's private-production V1 posture: real first-user bootstrap, robust iOS async UX, deterministic goal indicators, production-credible knowledge retrieval, hardened export custody, server-side scheduling/idempotency, and the missing iOS Settings/Trends/Memory/feedback surfaces.

**Architecture:** Keep the core PRD invariant untouched—deterministic features and safety rules remain authoritative; LLMs only explain bounded structured inputs. Each module is self-contained, ships with tests, and lands as its own commit on the feature branch.

**Tech Stack:** Python 3.12 + FastAPI + SQLModel + Alembic + arq/Redis, Swift 6 + SwiftUI + HealthKit + XCTest, dependency-free dashboard, pytest, Node `--test`.

---

## Global acceptance criteria

- `make lint`, `make typecheck`, `make test`, `make eval`, `make docs-check`, `npm test --prefix apps/dashboard`, and `swift test --package-path apps/ios` all pass.
- Every module has its own focused tests that pass before the module is committed.
- No raw health data, free-text notes, secrets, or export keys leak into logs, manifests, model-run metadata, or dashboard payloads.
- Deterministic feature/safety logic is never delegated to the LLM.
- Each module is committed separately with a Conventional Commit message and a short body explaining the change and verification.

---

## Module 1: First-user bootstrap and consent

### Context
The backend already creates a `User` when the first `POST /v1/data/consent` arrives, but this path is not explicitly verified end-to-end. A fresh server must be usable without manual DB seeding.

### Goals
1. Empty DB + consent request creates exactly one `User` and one active `ConsentRecord` atomically.
2. Health sync rejects a mismatched consent version and accepts the current active version.
3. Multiple users still cause the consent/data-control endpoints to fail closed (`409 ambiguous_user`).
4. iOS onboarding persists the server-returned consent version before the first sync.
5. Deployment docs describe the first-user bootstrap behavior.

### Files
- `apps/api/baseline_api/privacy/consent.py` (verify behavior)
- `apps/api/baseline_api/ingestion/sync_service.py`
- `apps/api/tests/test_data_controls.py`
- `apps/api/tests/test_health_sync_api.py`
- `apps/ios/Sources/BaselineApp/AppModel.swift`
- `apps/ios/Sources/BaselineCore/APIClient.swift`
- `apps/ios/Tests/BaselineCoreTests/BaselineCoreTests.swift`
- `docs/runbooks/deployment-readiness.md`

### Test plan
- Backend test: empty DB → record consent → assert one user, active consent version set.
- Backend test: sync with correct consent version accepted.
- Backend test: sync with stale consent version rejected.
- Backend test: seed two users → consent endpoint returns 409.
- iOS test: onboarding calls `recordConsent`, stores returned `consentVersion`, and uses it for sync.

### Tasks
- [ ] Step 1: Add backend tests for first-user bootstrap, consent-version acceptance, and multi-user fail-closed.
- [ ] Step 2: Run backend tests and fix any bootstrap edge cases.
- [ ] Step 3: Add iOS test verifying server consent is persisted before sync.
- [ ] Step 4: Verify `docs/runbooks/deployment-readiness.md` accurately describes first-user bootstrap; update if needed.
- [ ] Step 5: Run full verification gates.
- [ ] Step 6: Commit with message `feat(privacy): verify and harden first-user bootstrap`.

---

## Module 2: iOS briefing async UX and wake-aware reminders

### Context
The iOS client polls for the daily briefing but does not fully respect the backend's async job estimate, and background refresh is not tied to the user's wake time. There is no local reminder when a briefing is ready or when sync is stale.

### Goals
1. Briefing polling uses `estimated_completion_seconds * 2` as a deadline, with a floor of 60 s and a ceiling of 180 s, polling every 2 s.
2. The UI shows "analysis running" while the job is healthy and only falls back to the cached/offline briefing on failure or timeout.
3. A retry action is surfaced for timed-out jobs.
4. Background refresh is scheduled near the user's typical wake time, configured during onboarding or in Settings (default 07:00 local time).
5. An optional local notification reminds the user to sync/check-in in the morning.

### Files
- `apps/ios/Sources/BaselineApp/DailyBriefingView.swift`
- `apps/ios/Sources/BaselineApp/CheckInGoalViewModels.swift`
- `apps/ios/Sources/BaselineApp/BackgroundRefreshScheduler.swift`
- `apps/ios/Sources/BaselineApp/AppModel.swift`
- `apps/ios/Sources/BaselineCore/Models.swift`
- `apps/ios/Tests/BaselineAppTests/BaselineAppTests.swift`
- `apps/ios/App/Info.plist` (add `NSUserNotificationsUsageDescription`)

### Test plan
- Swift test: polling respects backend estimate and cap.
- Swift test: timeout shows retry and preserves cached briefing.
- Swift test: background refresh scheduling uses wake time.
- Swift test: notification authorization is requested and a morning reminder is scheduled.

### Tasks
- [ ] Step 1: Verify `DailyBriefingViewModel` polling uses backend estimate, 2 s interval, and capped deadline; add tests if coverage is missing.
- [ ] Step 2: Add retry UI state and action.
- [ ] Step 3: Add `wakeTime` field to the iOS consent model, defaulting to 07:00 local time (the full Settings tab is Module 7).
- [ ] Step 4: Use `wakeTime` to set the background refresh earliest-begin date in `BackgroundRefreshScheduler`.
- [ ] Step 5: Request `UserNotifications` authorization and schedule a morning reminder in `BackgroundRefreshScheduler` or `AppModel`.
- [ ] Step 6: Add/update Swift tests.
- [ ] Step 7: Run iOS tests and full verification gates.
- [ ] Step 8: Commit with message `feat(ios): estimate-aware briefing polling and wake reminders`.

---

## Module 3: Goal-specific deterministic indicators and tradeoffs

### Context
Goal indicators exist in `features/goals.py`, but the reasoning engine's goal tradeoffs may still be generic. Tradeoffs must cite concrete goal indicator evidence refs or explicitly state missing data. The PRD's optional sexual-health lifestyle indicators remain a private check-in toggle, not a standalone goal category; `long_term_wellness` is a general lifestyle-consistency proxy only.

### Goals
1. No placeholder-only goal hooks remain in `features/goals.py`.
2. Reasoning goal tradeoffs consume `goal_features.values.goal_indicators` and emit evidence refs.
3. Missing goal data produces low-confidence `unavailable` indicators with clear missing-data lists.
4. Hard safety and low-confidence signals still win over goal pressure.

### Files
- `apps/api/baseline_api/features/goals.py`
- `apps/api/baseline_api/reasoning/engine.py`
- `apps/api/baseline_api/features/feature_types.py`
- `apps/api/tests/test_features_load_density.py`
- `apps/api/tests/test_reasoning_engine.py`
- `packages/eval/reasoning_scenarios.py`

### Test plan
- Unit test: each PRD goal category (`vo2_max`, `strength`, `recovery`, `sleep`, `cognitive_performance`, `long_term_wellness`) produces a computed or unavailable indicator.
- Reasoning test: goal tradeoffs include evidence refs for active goals.
- Golden scenario: cognitive priority week shows cognitive indicator in tradeoffs.
- Golden scenario: missing strength data shows unavailable indicator and reduced confidence.

### Tasks
- [ ] Step 1: Audit `features/goals.py` for placeholders; all PRD categories already have indicators, so this step focuses on tests and removing any leftover placeholder-only hooks.
- [ ] Step 2: Update `_goal_tradeoffs` in `reasoning/engine.py` to use goal indicator evidence refs.
- [ ] Step 3: Add/update golden scenarios for goal categories and missing data.
- [ ] Step 4: Run feature and reasoning tests.
- [ ] Step 5: Run `make eval` and verify reasoning suites still pass.
- [ ] Step 6: Commit with message `feat(reasoning): concrete goal indicators and evidence-backed tradeoffs`.

---

## Module 4: Knowledge retrieval production credibility

### Context
External knowledge retrieval is wired but uses deterministic hash embeddings and a small starter corpus. The briefing builds a relatively static query. Production V1 needs dynamic query construction, a larger curated corpus, and real retrieval evals.

### Goals
1. Expand the curated corpus to at least 10 metadata-rich authoritative sources covering physical activity, sleep/recovery, strength training, HRV/recovery caveats, and wellness boundaries.
2. Construct the external retrieval query from high-level, non-personalized signals only: active goal categories, recommendation band, and general topics (e.g., "sleep debt", "training load", "recovery"). Never send raw feature values, HRV/RHR numbers, or personal notes to the embedding endpoint.
3. Keep `HashEmbeddingProvider` as the offline/test default; allow `HTTPEmbeddingProvider` to be configured via existing settings.
4. Add retrieval evals for citation relevance, personal/general evidence separation, disabled external knowledge, and unsupported medical claims.

### Files
- `packages/knowledge/starter_corpus.py`
- `packages/knowledge/embeddings.py`
- `apps/api/baseline_api/retrieval/knowledge.py`
- `apps/api/baseline_api/briefing/service.py`
- `apps/api/baseline_api/assistant/service.py`
- `packages/eval/retrieval_scenarios.py`
- `apps/api/tests/test_knowledge_retrieval.py`

### Test plan
- Retrieval test: dynamic query includes active goal categories and recommendation band; no raw feature values or personal notes are present.
- Retrieval test: personal evidence count is zero in external retrieval result.
- Retrieval test: external knowledge is skipped when consent disables it.
- Eval test: retrieval suites cover citation relevance and medical-claim handling.

### Tasks
- [ ] Step 1: Verify `starter_corpus.py` already has ≥10 authoritative sources with metadata; add any missing topic coverage if needed.
- [ ] Step 2: Add `build_external_knowledge_query()` helper using readiness inputs.
- [ ] Step 3: Wire dynamic query into `briefing/service.py` and `assistant/service.py`; for assistant queries without a recommendation band, fall back to active goals and question-derived topics extracted via a small keyword allow-list (e.g., "sleep", "recovery", "vo2", "strength"), not an LLM.
- [ ] Step 4: Add retrieval eval suites.
- [ ] Step 5: Run knowledge tests and `make eval`.
- [ ] Step 6: Commit with message `feat(retrieval): dynamic external knowledge retrieval and expanded corpus`.

---

## Module 5: Export/key custody and single-user production hardening

### Context
Export encryption keys are returned to the client once and previously lived only in an in-memory dict inside the export service, so they were lost on process restart and could not support a future server-side custody model. Single-user resolution is also duplicated across services. Production needs a consistent client-custody model, an `ExportKeyStore` abstraction ready for future server-side custody, and a centralized single-user context.

### Goals
1. Export keys are returned once in the API response (existing contract) and are never persisted server-side in manifests, logs, model-run metadata, dashboard data, or an export key store. The current model is client custody.
2. Provide an `ExportKeyStore` abstraction (`ExportKeyStore` protocol with `MemoryExportKeyStore` and `RedisExportKeyStore` backends) as future infrastructure for optional server-side custody. Do not wire it into the active create-export flow until a documented consumer (e.g., a key-retrieval endpoint) is added.
3. Keep the existing `LocalExportStore` for encrypted file storage; reject temp-backed storage in production (config already enforces `EXPORT_STORAGE_DIR`).
4. Add a `SingleUserContext` FastAPI dependency used by API routes to resolve the single user and pass it into services. Keep the existing session-only helper for worker/non-request callers so typed error factories are preserved. After this module, new API routes should use `SingleUserContext` instead of calling `get_single_user(session)` directly.
5. Document the client-custody export model in `.env.example`, `docs/privacy/data-flow.md`, and `docs/runbooks/deployment-readiness.md`.
6. Add a runtime guard that rejects temp-backed export storage when `APP_ENV` is `staging` or `production`.
7. Add tests proving key material is absent from manifest, logs, and disclosures, and that production config with `EXPORT_STORAGE_DIR=None` fails fast.

### Files
- `apps/api/baseline_api/privacy/export.py`
- `apps/api/baseline_api/privacy/delete.py`
- `apps/api/baseline_api/privacy/disclosure.py`
- `apps/api/baseline_api/privacy/key_store.py` (new)
- `apps/api/baseline_api/privacy/user.py`
- `apps/api/baseline_api/db/session.py` or `apps/api/baseline_api/api/deps.py` (new)
- `apps/api/baseline_api/ingestion/sync_service.py`
- `apps/api/baseline_api/checkin/service.py`
- `apps/api/baseline_api/goals/service.py`
- `apps/api/baseline_api/briefing/service.py`
- `apps/api/baseline_api/assistant/service.py`
- `apps/api/baseline_api/config.py`
- `.env.example`
- `apps/api/tests/test_data_controls.py`
- `apps/api/tests/test_redaction.py` (new)

### Test plan
- Export test: key is not present in manifest JSON.
- Export test: Redis-backed key store returns key before expiry and returns `None` after expiry.
- Export test: `ModelDisclosureService` output does not contain export key material.
- Single-user test: all data-control and sync endpoints resolve the same user on a fresh DB after consent.

### Tasks
- [ ] Step 1: Design `ExportKeyStore` protocol and implement `MemoryExportKeyStore` and `RedisExportKeyStore` as future infrastructure.
- [ ] Step 2: Remove in-memory `_exports` key storage from `DataExportService`; return the key only in the API response. Update `LocalExportStore.decrypt()` and its callers/tests to accept the key as an argument.
- [ ] Step 3: Add a runtime guard rejecting temp-backed export storage in staging/production.
- [ ] Step 4: Add `SingleUserContext` dependency and update ingestion, check-in, goals, briefing, assistant, and privacy services to use it.
- [ ] Step 5: Document the client-custody export model in `.env.example`, `docs/privacy/data-flow.md`, and `docs/runbooks/deployment-readiness.md`.
- [ ] Step 6: Add tests for key absence from manifest/logs/disclosures, key-store abstraction expiry, and production temp-storage rejection.
- [ ] Step 7: Run data-control and privacy tests.
- [ ] Step 8: Commit with message `feat(privacy): durable export key store and centralized single-user context`.

---

## Module 6: Backend scheduler, briefing idempotency, and automated memory compaction

### Context
Daily briefings are triggered by client check-ins, not by a server-side scheduler. Re-enqueueing a daily job can create duplicate recommendations and memory summaries. Weekly/monthly/quarterly memory compaction has no scheduled driver.

### Goals
1. `run_daily_job` follows a clear state machine: no job → create; queued/running → return existing; completed → return existing unless `force_recompute=True`, then create a new run; failed → retry up to `DAILY_BRIEFING_MAX_RETRIES` (default 2), tracked by a `retry_count` column on `DailyAnalysisJob`, then leave failed.
2. Add arq cron jobs as **fallback/scheduled backstops**: a daily briefing cron wrapper (`daily_briefing_cron`) creates a `DailyAnalysisJob` for today if none exists and runs it; weekly/monthly/quarterly memory compaction cron wrappers do the same. Defaults: daily briefing 08:00 UTC, weekly Monday 06:00 UTC, monthly 1st 06:00 UTC, quarterly 1st of quarter 06:30 UTC (offset from monthly to avoid a race on quarter boundaries). The primary trigger remains the iOS client post-sync enqueue; the cron catches missed days.
3. Worker startup still marks stale `running` jobs as failed.
4. Add a stale-briefing operational alert evaluated by the daily briefing cron/health check when no briefing has been generated for the current UTC day by `STALE_BRIEFING_ALERT_HOUR_UTC` (default 12); surface the alert via `/v1/observability/alerts` and the dashboard safety-events panel.

### Files
- `apps/api/baseline_api/briefing/service.py`
- `apps/api/baseline_api/briefing/worker.py`
- `apps/api/baseline_api/db/models/assessment.py` (add `retry_count` to `DailyAnalysisJob`)
- `apps/api/baseline_api/worker.py`
- `apps/api/baseline_api/memory/service.py`
- `apps/api/baseline_api/memory/worker.py` (new)
- `apps/api/baseline_api/observability/alerts.py`
- `apps/api/baseline_api/config.py`
- `.env.example`
- `apps/api/alembic/versions/` (migration for `retry_count`)
- `apps/api/tests/test_briefing_api.py`
- `apps/api/tests/test_memory_compiler.py`

### Test plan
- Test: re-enqueueing a completed daily briefing job returns the existing result without creating duplicates.
- Test: worker cron schedule includes daily briefing and memory compaction jobs.
- Test: stale `running` jobs are marked failed on worker startup.
- Test: stale-briefing alert fires when expected.

### Tasks
- [ ] Step 1: Add `get_or_create_daily_job_for_date()` helper used by both the API route and the cron wrapper; add status-guard idempotency to `run_daily_job` with the state machine above.
- [ ] Step 2: Add `retry_count` column to `DailyAnalysisJob`, create an Alembic migration, and add `DAILY_BRIEFING_MAX_RETRIES` setting.
- [ ] Step 3: Add memory compaction worker functions `compact_weekly_memory`, `compact_monthly_memory`, and `compact_quarterly_memory`.
- [ ] Step 4: Add cron wrapper functions (`daily_briefing_cron`, etc.) that call `get_or_create_daily_job_for_date()` and delegate to the existing worker functions; register the cron schedule in `worker.py`.
- [ ] Step 5: Add `STALE_BRIEFING_ALERT_HOUR_UTC` and `DAILY_BRIEFING_MAX_RETRIES` settings and document them in `.env.example`.
- [ ] Step 6: Add stale-briefing alert evaluator.
- [ ] Step 7: Add/update tests.
- [ ] Step 8: Run backend tests and full gates.
- [ ] Step 9: Commit with message `feat(worker): idempotent daily briefing scheduler and memory compaction`.
---

## Module 7: iOS Settings, feedback, and data controls

### Context
The iOS app has no Settings tab. Export, delete-all, disable LLM, consent history, model disclosures, and privacy-mode changes are unreachable. There is also no way to rate a recommendation or record the action actually taken.

### Goals
1. Add a `SettingsView` accessible from the tab bar.
2. Expose: privacy mode change, data export (request + download + decrypt + share), delete-all confirmation, delete individual check-ins and notes, disable external LLM, consent history viewer, model disclosures viewer, current LLM provider/model (read-only; runtime changes are operator-controlled via server config).
3. Expose full consent revocation: revoke cloud processing, external LLM, raw note processing, and individual health categories.
4. Add briefing feedback UI: rating, action taken, outcome notes.
5. Wire all flows to the existing `BaselineCore` API clients.

### Files
- `apps/ios/Sources/BaselineApp/SettingsView.swift` (new)
- `apps/ios/Sources/BaselineApp/ConsentManagementView.swift` (new)
- `apps/ios/Sources/BaselineApp/RootView.swift`
- `apps/ios/Sources/BaselineApp/DailyBriefingView.swift`
- `apps/ios/Sources/BaselineApp/CheckInGoalViewModels.swift`
- `apps/ios/Sources/BaselineCore/APIClient.swift`
- `apps/ios/Sources/BaselineCore/Models.swift`
- `apps/ios/Tests/BaselineAppTests/BaselineAppTests.swift`
- `apps/api/baseline_api/api/data.py` (LLM settings endpoints)
- `apps/api/baseline_api/schemas/api.py`

### Test plan
- Swift test: Settings view renders and toggles send correct API requests.
- Swift test: export request/response round-trips and decryption works.
- Swift test: delete-all confirmation sends the delete request.
- Swift test: briefing feedback encodes rating and action taken.

### Tasks
- [ ] Step 1: Create `SettingsView` and add it to the tab bar.
- [ ] Step 2: Implement export flow in Settings.
- [ ] Step 3: Implement delete-all confirmation flow.
- [ ] Step 4: Expose individual check-in and note deletion using existing `DELETE /v1/data/checkins/{id}` and `/v1/data/checkins/{id}/note` endpoints; extend `CheckInAPIClient`/`DataControlsAPIClient` protocols and `URLSessionHealthSyncAPIClient` with the note-deletion method.
- [ ] Step 5: Implement `ConsentManagementView` with disable external LLM, revoke cloud processing, revoke raw note processing, revoke health categories, and consent history viewer.
- [ ] Step 6: Add `LLMSettingsResponse` schema, `GET /v1/data/llm-settings` route in `apps/api/baseline_api/api/data.py`, and corresponding Swift model; document the endpoint in `docs/architecture/api-contracts.md`.
- [ ] Step 7: Expose the read-only LLM provider/model values in Settings (operator-controlled, not user-editable).
- [ ] Step 8: Add `recommendation_id` to `DailyBriefingResponse` schema and the iOS `DailyBriefingResponse` model; populate it from the existing `_persist_recommendation` call without reordering the pipeline.
- [ ] Step 9: Add `RecommendationFeedbackRequest`/`Response` models, extend `DailyBriefingAPIClient` protocol, and add `submitRecommendationFeedback` to `URLSessionHealthSyncAPIClient`.
- [ ] Step 10: Add briefing feedback UI and wire to API.
- [ ] Step 11: Add Swift UI/flow tests.
- [ ] Step 12: Run iOS tests and full verification gates.
- [ ] Step 13: Commit with message `feat(ios): Settings, data controls, and briefing feedback`.

---

## Module 8: iOS Trends and Memory views

### Context
PRD §24 requires Trends and Memory views. The backend has memory summaries but no list endpoint, and no aggregated trend endpoint for feature history.

### Goals
1. Add backend endpoint `/v1/memory/summaries` listing memory summaries by period type with pagination.
2. Add backend endpoint `/v1/trends` returning windowed aggregates (mean/latest/count as appropriate) over `DerivedDailyFeature` rows for sleep, HRV/RHR, training load, VO2 max, recovery, and goal progress over 7/30/90-day windows. `goal_indicator_completeness` is extracted from nested `goal_features`. Define schemas in code first, then document the endpoints in `docs/architecture/api-contracts.md` after route registration so `docs-check` passes.
3. Add iOS `MemoryView` showing daily/weekly/monthly summaries, learned patterns, and correction/deletion controls.
4. Add iOS `TrendsView` with charts or list visualizations of the aggregated history.

### Files
- `apps/api/baseline_api/api/memory.py` (new)
- `apps/api/baseline_api/api/trends.py` (new)
- `apps/api/baseline_api/memory/service.py`
- `apps/api/baseline_api/db/repositories/memory.py`
- `apps/api/baseline_api/db/repositories/features.py`
- `apps/api/baseline_api/app.py` (register routers)
- `apps/api/tests/test_memory_api.py` (new)
- `apps/api/tests/test_trends_api.py` (new)
- `apps/ios/Sources/BaselineApp/MemoryView.swift` (new)
- `apps/ios/Sources/BaselineApp/TrendsView.swift` (new)
- `apps/ios/Sources/BaselineApp/RootView.swift`
- `apps/ios/Sources/BaselineCore/APIClient.swift`
- `apps/ios/Sources/BaselineCore/Models.swift`
- `apps/ios/Tests/BaselineAppTests/BaselineAppTests.swift`

### Test plan
- Backend test: `/v1/memory/summaries` returns summaries for the authenticated user.
- Backend test: `/v1/trends` returns feature history for requested windows.
- Swift test: MemoryView renders summaries and supports delete/correct actions.
- Swift test: TrendsView renders feature history.

### Tasks
- [ ] Step 1: Define request/response Pydantic/Swift schemas for `/v1/memory/summaries` (query params: `period_type`, `limit`), `/v1/trends` (query params: `window_days` ∈ {7,30,90}, `metrics[]` ∈ {sleep_debt_hours, hrv_deviation_pct, rhr_deviation_pct, training_load_acute, training_load_chronic, vo2_max_recent, recovery_level, goal_indicator_completeness}), and memory correction (`POST /v1/memory/summaries/{id}/correct`). Document the endpoints in `docs/architecture/api-contracts.md` only after they are registered.
- [ ] Step 2: Add memory repository/service methods to list summaries and correct a summary.
- [ ] Step 3: Add `/v1/memory/summaries` endpoint and `POST /v1/memory/summaries/{id}/correct` endpoint.
- [ ] Step 4: Add feature-history repository/service and `/v1/trends` endpoint.
- [ ] Step 5: Register new routers in `app.py`; then document the new endpoints in `docs/architecture/api-contracts.md`.
- [ ] Step 6: Add `BaselineCore` API client methods for memory summaries, memory correction, memory summary deletion (reusing existing `DELETE /v1/data/memory-summaries/{id}`), and trends.
- [ ] Step 7: Create `MemoryView` and `TrendsView` and add them to the tab bar.
- [ ] Step 8: Wire iOS API client methods to views.
- [ ] Step 9: Add backend and iOS tests.
- [ ] Step 10: Run full verification gates.
- [ ] Step 11: Commit with message `feat(ios,api): Trends and Memory views with backend endpoints`.

---

## Final close-out

After all modules land:

- [ ] Run `make migrate` because Module 6 adds a `retry_count` column to `DailyAnalysisJob` (Alembic migration required).
- [ ] If new eval suites were added, update the suite inventory in `scripts/check_docs.py` and regenerate `artifacts/eval/evaluation-report.md` so `make docs-check` passes.
- [ ] Run the complete verification matrix:
  ```bash
  make lint
  make typecheck
  make test
  make eval
  make docs-check
  npm test --prefix apps/dashboard
  swift test --package-path apps/ios
  ```
- [ ] Update `docs/runbooks/deployment-readiness.md` and `docs/architecture/api-contracts.md` with any new endpoints.
- [ ] Run `docs-check` again.
- [ ] Dispatch a final code-review subagent over the entire feature branch.
- [ ] Open a PR (or propose a squash merge) to `main`, run CI, and ask the user for approval before merging.

---

## Assumptions and non-goals

- Scope remains a private single-user deployment; closed-beta multi-tenant auth is out of scope.
- No new heavy dependencies unless required for configured production embeddings or export key storage; offline/test paths stay dependency-light.
- Existing core invariants (deterministic first, SQL for personal data, RAG for external knowledge only, safety gate after generation) are preserved.
- Model fallback and degraded-mode behavior are already implemented in the LLM orchestrator (local deterministic fallback, cloud-disabled degraded output). This plan hardens the surrounding UX and operations but does not redesign the fallback mechanism.
- Cost/latency observability scaffolding exists; this plan wires missing metrics where needed but does not build a new commercial billing dashboard.
