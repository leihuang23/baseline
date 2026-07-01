# P3-08: iOS — daily briefing, trace view & follow-up

**Phase:** 3 — Reasoning, briefing, safety | **Depends on:** P3-06 | **Parallelizable with:** P3-07 | **Surface:** iOS (thin)

## Context (self-contained)
Third thin iOS surface — this closes the MVP loop the user sees each morning. It renders the briefing (§18 fields), exposes the **trace view** (a portfolio differentiator: feature values, rules fired, retrieved memory, model metadata), supports follow-up questions, and works offline for the last briefing (NFR-008). Keep it clean and minimal.

## Goal
Build the SwiftUI daily-briefing screen, the trace/inspection view, and a follow-up question entry wired to the briefing + assistant APIs, with offline viewing of the latest briefing.

## Scope
In:
- Briefing screen rendering every §18/§17.4 field: readiness state, main recommendation, evidence, confidence, uncertainty, goal tradeoffs, alternatives, data freshness, safety note, "what would change my mind" (FR-055).
- "Trigger sync + generate briefing" flow (calls `POST /v1/analysis/daily`, polls, then `GET /v1/briefings/{date}`); clear loading/degraded states.
- **Trace view** (FR-058): data freshness, feature values, rules fired, retrieved memory, external sources, model metadata — read-only inspection.
- Follow-up question entry → `POST /v1/assistant/query`; render answer with personal evidence + citations + safety status (FR-059).
- Offline: display last-generated briefing when network/generation unavailable (NFR-008); show staleness/freshness prominently.
- Disclaimer/safety note placement near medical-adjacent content (§19.2).

Out:
- Trends/memory/settings screens (later); backend logic (P3-06/07).

## Deliverables
- SwiftUI briefing + trace + follow-up screens under `apps/ios/`, API client methods, view models.

## Acceptance criteria
- [ ] All §18 fields render; safety note + freshness always visible.
- [ ] Generate-briefing flow handles loading, success, and degraded (deterministic-only) states.
- [ ] Trace view shows feature values, rules fired, retrieved memory, and model metadata.
- [ ] Follow-up returns and renders an evidence-backed answer; offline shows the last briefing.

## Tests required
- View-model tests for generate→poll→fetch, degraded state, and follow-up.
- Offline-last-briefing test; snapshot test asserting safety note + freshness presence.

## PRD references
§24.1 Daily Briefing + Trace View, §12.7 FR-054–060, NFR-008, §19.2.
