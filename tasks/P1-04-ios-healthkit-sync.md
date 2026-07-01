# P1-04: iOS — HealthKit auth, onboarding & incremental sync

**Phase:** 1 — Data ingestion MVP | **Depends on:** P1-01 | **Parallelizable with:** P1-02, P1-03 | **Surface:** iOS (thin)

## Context (self-contained)
Baseline's iOS app is a **thin data collector + briefing viewer** — reviewers value the backend/AI more than UI, so keep it minimal and correct. This slice is the first iOS surface: onboarding boundary + consent, HealthKit permissions with per-type rationale, and incremental sync to `POST /v1/health/sync`. Backend sync API + anchors already exist (P1-01). Stack: SwiftUI, HealthKit, Keychain + Data Protection, async/await, URLSession.

## Goal
Ship an iOS app that explains the product boundary, lets the user pick a privacy mode, requests only the needed HealthKit read permissions (degrading gracefully on partial grants), and performs anchored incremental sync with clear "Sync now"/"Last synced" controls.

## Scope
In:
- Onboarding: product-boundary screen ("wellness decision support, not medical diagnosis/treatment", FR-001), privacy-mode selection (local-only / cloud-assisted / hybrid, FR-003 wording per permission), demo-mode entry (FR-007).
- HealthKit authorization for enabled features only (sleep, workouts, steps, HRV, resting HR, VO2 max), each with a shown rationale (FR-002/003); continue with partial permissions and degrade gracefully (FR-004).
- Consent capture: record consent version + enabled categories + processing mode; send `consent_version` on sync (FR-005/006).
- Anchored incremental read using HKAnchoredObjectQuery; persist the anchor; push batches to `/v1/health/sync`; handle `next_anchor`.
- Manual "Sync now" + "Last synced" display; interrupted-sync resume; local encrypted cache (Data Protection / Keychain for tokens).
- Attempt background refresh where allowed but never rely on it (FR-013/014).

Out:
- Check-in UI + goals (P2-05); briefing UI (P3-08); the sync/normalization backend (P1-01/02).

## Deliverables
- SwiftUI app under `apps/ios/` with onboarding, HealthKit sync service, anchor persistence, and settings entry for sync.

## Acceptance criteria
- [ ] Permission flow shows per-type rationale; partial grants still let the app function (no crash, clear degraded state).
- [ ] Incremental sync sends only new samples since the stored anchor; interrupted sync resumes without duplicates.
- [ ] "Last synced" reflects reality; "Sync now" works on demand.
- [ ] Consent version + categories recorded and sent; no secrets in the bundle; sensitive data uses Data Protection.
- [ ] Demo mode launches with synthetic data and no HealthKit access.

## Tests required
- Unit tests for anchor persistence + batch building; mocked-HealthKit permission-flow tests (full + partial).
- Interrupted-sync resume test; "no secrets in bundle" check.

## PRD references
FR-001–007, FR-008/013/014/015, §24.1 Onboarding/Morning, §22.2 mobile tests, NFR-003/004.
