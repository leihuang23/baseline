# P6-01: Portfolio demo mode & private-data-leak tests

**Phase:** 6 — Portfolio packaging | **Depends on:** P0-03, P3-06, P5-03 | **Parallelizable with:** P6-02 | **Surface:** backend + dashboard

## Context (self-contained)
Baseline is a portfolio project: it must be **fully demoable on synthetic data with zero private-data exposure.** A reviewer should experience the whole loop (sync → check-in → briefing → trace → memory → dashboard) on a reproducible synthetic persona, and automated tests must guarantee no real/private data can leak into demo mode.

## Goal
Deliver an end-to-end demo mode driven by the 60-day synthetic persona (P0-03), a scripted reproducible walkthrough, and a private-data-leak test suite.

## Scope
In:
- A `demo` mode/flag that seeds the synthetic 60-day persona and runs the full pipeline (ingestion → features → reasoning → briefing → memory) with no external calls required (mock/record LLM).
- A reproducible, scripted walkthrough (CLI or make target) that produces a briefing, a trace, memory summaries, and dashboard views on demand — deterministic across runs.
- **Private-data-leak tests** (§27.10): assert demo mode + dashboard + exports contain only synthetic data; assert no real PII, secrets, or raw notes appear anywhere in demo artifacts.
- At least 5 pre-baked demo scenarios (from §22.2) selectable for review (§8.5).
- Ensure demo works without production secrets (interview-safe).

Out:
- Written architecture/README docs (P6-02).

## Deliverables
- Demo-mode seeding + walkthrough script + leak-test suite (registered in `packages/eval`).

## Acceptance criteria
- [ ] `make demo` (or equivalent) reproducibly runs the full loop on synthetic data with no secrets and no live API.
- [ ] ≥5 scenarios selectable; briefing + trace + memory + dashboard all populated in demo mode.
- [ ] Leak tests prove zero real/private data in demo artifacts, dashboard, and exports.
- [ ] Deterministic across runs (same seed → same demo).

## Tests required
- End-to-end demo pipeline test; determinism test.
- Private-data-leak suite (demo artifacts, dashboard, export) — must be part of CI.

## PRD references
§8.5 Portfolio metrics, §11.1 (demo mode), §22.2/§27.10 (leak tests), NFR-016, §25 Phase 6.
