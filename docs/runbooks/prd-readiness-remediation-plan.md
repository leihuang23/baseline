# PRD Readiness Remediation Plan

## Summary

Baseline is functionally strong as a private, portfolio-grade single-user system, but the PRD is not fully satisfied for a fresh real deployment or production-readiness review. This plan closes the review findings in priority order:

1. Fix first-user onboarding/consent/bootstrap so a fresh server can sync real HealthKit data without manual DB seeding.
2. Make iOS daily briefing polling match the backend async job contract.
3. Replace goal-module placeholders with deterministic, evidence-backed goal indicators and reasoning tradeoffs.
4. Upgrade external knowledge retrieval from demo-grade plumbing to a more production credible curated retrieval path.
5. Harden export storage/key custody and document the real single-user production boundary.
6. Add iOS Trends and Memory views so users can browse structured memory summaries and compare weeks.
7. Verify the whole system with targeted tests plus full gates.

## Context And Goals

The prior broad review found these gaps:

- Fresh onboarding only stores consent locally; backend consent and sync require an existing single user.
- Goal-specific modules mostly expose VO2 and placeholder extension hooks.
- The backend is intentionally single-user and not closed-beta multi-tenant ready.
- External knowledge retrieval exists but uses a tiny starter corpus, hash embeddings, and a fixed query.
- iOS briefing polling times out much sooner than the backend's async estimate.
- Export is encrypted but temp-file/local-key-custody oriented.

Primary goal: make the implementation truthful and robust for the PRD's intended private production-oriented MVP/V1 posture.

Non-goal: build full closed-beta multi-user authentication. That belongs to a separate V2/closed-beta task unless explicitly prioritized later.

## Workstreams

### 1. First-User Bootstrap And Consent

- Extend `ConsentRecordRequest` with optional `privacy_mode`.
- Update backend consent recording so an empty database creates the first `User` and active `ConsentRecord` atomically, one-user deployments preserve current update behavior, and multiple users continue to fail closed.
- Infer `privacy_mode` only when missing: `local_only` when cloud processing is disabled, otherwise `cloud_assisted` when external LLM is enabled and `hybrid` for server processing without external LLM.
- Add iOS consent submission during onboarding and persist the server-returned consent version before HealthKit sync.
- Update deployment docs to describe first-user bootstrap and private single-user limits.

### 2. iOS Briefing Async UX

- Replace fixed short polling with estimate-aware polling:
  - Initial deadline: `max(60s, estimated_completion_seconds * 2)`, capped at `180s`.
  - Poll interval: `2s` while queued/running.
  - Show "analysis still running" while the job is healthy.
- Only show offline fallback when the backend returns a failed job, the deadline expires, or fetching the final briefing fails after completion.
- Add user-visible retry for timed-out jobs.

### 3. Goal-Specific Indicators And Tradeoffs

- Replace `goal_hooks.extension_points` with computed deterministic indicators for `vo2_max`, `strength`, `cognitive_performance`, `long_term_wellness`, `recovery`, and `sleep`.
- Update feature assembly so goal features receive already-computed sleep, cardio, training-load, recovery, workout, VO2, and optional check-in inputs.
- Update reasoning so goal tradeoffs cite concrete goal indicators and evidence refs, or clearly state which data is missing.
- Preserve the safety invariant that hard safety and low-confidence signals win over goal pressure.

### 4. Knowledge Retrieval Production Credibility

- Expand the curated corpus to at least 10 metadata-rich authoritative sources across physical activity, sleep/recovery, strength training, HRV/recovery caveats, and general wellness boundaries.
- Keep deterministic hash embeddings as the offline/test fallback, while configuring a production embedding provider behind the existing abstraction.
- Replace the fixed briefing retrieval query with a query built from readiness state, recommendation band, active goals, risk flags, uncertainty, and requested external knowledge scope.
- Add retrieval evals for citation relevance, personal/general evidence separation, disabled external knowledge, and unsupported medical claims.

### 5. Export And Single-User Production Hardening

- Add explicit export settings: `EXPORT_STORAGE_DIR`, `EXPORT_RETENTION_HOURS`, and `EXPORT_CLEANUP_ON_START`.
- Stop defaulting production exports to temp storage unless `APP_ENV` is `local` or `test`.
- Keep export files encrypted and server downloads encrypted only.
- Ensure export keys are returned once in the create response, never stored in manifests, logs, traces, model runs, or dashboard data.
- Add cleanup for expired exports and tests proving encrypted files and manifests are removed.
- Centralize single-user resolution for privacy, ingestion, goals, check-ins, and briefing services.
- Update deployment docs to state private single-user deployments are supported, multiple users fail closed, and closed beta requires account-level auth/user resolution before launch.

### 6. iOS Trends And Memory Views

- Add `GET /v1/data/memory-summaries` to return the single user's structured memory summaries, optionally filtered by `period_type`.
- Return summary fields: id, period_type, start/end dates, observations, hypotheses, confidence, summary_version, source_refs, and redaction hints.
- Add iOS `MemoryAPIClient` protocol and `URLSessionHealthSyncAPIClient` implementation.
- Add an iOS "Memory" tab that lists daily, weekly, monthly, and quarterly summaries, distinguishes observations from hypotheses, and shows confidence/source refs.
- Add an iOS "Trends" section within the Memory tab that surfaces week-over-week comparisons (e.g., latest weekly summary vs. prior weekly summary) and answers "how was this week different from last week?" using structured memory only.
- Wire deletion of an individual memory summary through the existing data-controls endpoint and update the list optimistically.
- Keep deterministic backend memory as the source of truth; the iOS layer only renders and deletes, never computes trends.

## Test Plan

### Targeted Backend Tests

- Empty DB plus `POST /v1/data/consent` creates exactly one user and active consent.
- Repeated consent update on one user revokes/replaces active consent as today.
- Multiple users still fail closed.
- Health sync succeeds after first-user consent without manual seed.
- Health sync rejects stale/mismatched consent versions.
- Golden tests cover each goal category.
- Missing goal data produces explicit unavailable/low-confidence indicators.
- No `extension_points` placeholders remain in generated goal features.
- Reasoning tradeoffs cite concrete goal feature refs.
- Dynamic retrieval queries include active goals and risk flags.
- External knowledge stays absent in local-only/no-consent mode.
- Production config rejects temp export storage.
- Expired exports are cleaned up.
- Key material is absent from manifest, logs, model disclosures, and dashboard payloads.

### Targeted iOS Tests

- Onboarding calls `recordConsent`, stores returned consent version, then sync uses that version.
- Consent failure keeps cloud/hybrid onboarding retryable.
- Briefing polling respects backend estimates and does not timeout after roughly 1.5 seconds.
- Timed-out jobs show retryable state and preserve latest cached briefing.
- `GET /v1/data/memory-summaries` returns only the single user's summaries and respects `period_type` filter.
- Memory summary list excludes raw sensitive notes when consent forbids them.
- iOS Memory view renders observations, hypotheses, confidence, and period labels.
- iOS Trends view renders a week-over-week comparison when at least two weekly summaries exist.
- Deleting a memory summary from iOS updates the list and calls the backend delete endpoint.

### Full Verification Gates

Run, in order:

```bash
uv run ruff format --check .
make lint
make typecheck
make test
make eval
make docs-check
npm test --prefix apps/dashboard
swift test --package-path apps/ios
```

If local DB or Swift cache sandboxing blocks verification, rerun the same command with the minimum required approval and record the reason.

## Acceptance Criteria

- A fresh database can complete: iOS onboarding, server consent, HealthKit sync, and normalization enqueue with no manual DB seed.
- `docs/runbooks/deployment-readiness.md` accurately describes first-user bootstrap and single-user limits.
- Goal features contain computed indicators for all PRD V1 goal categories; no placeholder-only hook list remains.
- Daily reasoning goal tradeoffs include concrete evidence refs or explicit missing-data explanations.
- External knowledge retrieval uses curated metadata-rich sources and dynamic query construction.
- iOS briefing generation waits according to the backend async job contract and does not prematurely show offline fallback.
- Export storage is configurable, production-safe by default, expiry-cleaned, and key material is not persisted server-side.
- Single-user limitations are centralized, tested, and documented as a private-deployment boundary.
- iOS users can browse structured memory summaries and delete individual summaries.
- iOS Trends surfaces a week-over-week comparison from backend memory without local computation.
- Full verification gates pass.
- Worktree is clean after implementation except for intentional committed changes.

## Assumptions And Defaults

- Scope remains private single-user production hardening, not full multi-tenant closed beta auth.
- Do not add new dependencies unless required for configured production embeddings or export storage; deterministic offline/test paths remain dependency-light.
- Preserve the core PRD invariant: deterministic features and safety rules remain authoritative; LLMs explain but do not compute or override.
