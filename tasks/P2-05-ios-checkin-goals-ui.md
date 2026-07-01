# P2-05: iOS — morning check-in & goal setup UI

**Phase:** 2 — Feature engine & check-in | **Depends on:** P2-01, P3-01 | **Parallelizable with:** P2-04 | **Surface:** iOS (thin)

## Context (self-contained)
Second thin iOS surface for Baseline. The morning check-in must be completable in **under one minute** and feed `POST /v1/checkins/daily`; goal setup feeds the goal-management API (P3-01). Keep UI minimal and correct — this is a portfolio backend showcase, not a consumer design exercise. Sensitive lifestyle indicators are optional and clearly private.

## Goal
Build the SwiftUI check-in form and a simple goal-setup screen, wired to the check-in and goals APIs, with sensitive data private-by-default.

## Scope
In:
- Check-in form: energy, mood, soreness, stress, perceived recovery, food quality (quick sliders/steppers); flags for alcohol, caffeine, illness, injury, travel; optional free-text note; optional high-level lifestyle indicators clearly marked private (FR-022/023/025).
- Under-one-minute UX: sensible defaults, minimal required fields, fast submit; edit/delete an existing day's check-in (FR-021/026).
- Local privacy affordance: show which fields stay on-device vs may be summarized for cloud processing, honoring the user's privacy mode.
- Goal setup: create/list/pause goals across categories (cognitive performance, VO2 max, strength, recovery, sleep, long-term wellness) with priority, horizon, success indicator, constraints (FR-028/029/030/032).
- Wire to `POST /v1/checkins/daily` and the goals API; optimistic UI + error handling.

Out:
- Feature/reasoning logic; briefing UI (P3-08); goal-conflict computation (backend P3-01/P3-02).

## Deliverables
- SwiftUI check-in + goals screens under `apps/ios/`, plus API client methods and view models.

## Acceptance criteria
- [ ] A full check-in is completable in <1 min with defaults; partial submit works; edit/delete works.
- [ ] Sensitive/lifestyle fields are optional, off by default, and visibly private.
- [ ] Goals can be created, listed, and paused with all required attributes.
- [ ] Network failures surface clear errors; no raw sensitive note is sent when privacy mode forbids it.

## Tests required
- View-model unit tests for check-in submit (full/partial), edit/delete, and goal CRUD.
- Snapshot/UI test that required-field count keeps check-in under the 1-minute bar.

## PRD references
§17.2, FR-021–032, §24.1 Morning, §12.4 Goal Management.
