# P5-04: Cost/latency monitoring, model fallback & degraded modes

**Phase:** 5 — Knowledge retrieval & eval dashboard (V1) | **Depends on:** P3-04, P3-06, P0-06 | **Parallelizable with:** P5-01/02 | **Surface:** backend

## Context (self-contained)
Baseline must keep **cost per daily briefing visible and bounded**, survive provider outages, and have a **clear degraded mode** when any stage fails — the daily briefing path must remain useful even without external RAG or a healthy LLM provider. Basic cost logging exists on `ModelRun` (P3-04); this slice adds budgets, alerts, fallback, and system-wide degraded behavior.

## Goal
Implement per-run/model/feature cost + latency aggregation with budgets and alerts, model provider fallback, and explicit degraded-mode behavior across sync, feature computation, retrieval, and LLM generation.

## Scope
In:
- Cost + latency aggregation per run / per model / per feature (NFR-014); expose to the dashboard (P5-03).
- Budgets + alerts: alert when cost exceeds a configured budget, when model-provider failures exceed a threshold, when schema validation fails repeatedly, when daily briefing generation fails, when sync failures exceed threshold, when a deletion fails (§23.3).
- **Model fallback**: on provider failure, route to a fallback provider/model; record the fallback in `ModelRun` (FR-078 operator story, §21.3).
- **Degraded modes** (NFR-011): define + implement behavior when sync / feature computation / retrieval / LLM each fail — the deterministic briefing must still be served (NFR-007), freshness clearly flagged.
- Runbook stubs (§23.4) for the failure modes referenced by alerts.

Out:
- Dashboard visualization (P5-03); the LLM orchestrator internals (P3-04, extended here only for fallback hooks).

## Deliverables
- `baseline_api/observability/cost.py` + `.../alerts.py` + degraded-mode policy in the briefing pipeline + `docs/runbooks/`.

## Acceptance criteria
- [ ] Cost + latency aggregated per run/model/feature and queryable; budgets enforced with alerts.
- [ ] Provider failure triggers fallback (recorded in ModelRun); repeated failures alert.
- [ ] Each stage's degraded mode is implemented + tested; deterministic briefing always available.
- [ ] Runbooks exist for each alerting failure mode.

## Tests required
- Cost-aggregation + budget-alert tests; provider-fallback test (primary down → fallback used + logged).
- Degraded-mode tests for sync/feature/retrieval/LLM failure; deterministic-briefing-still-served test.

## PRD references
§23.1/23.3/23.4, §21.3 routing/fallback, NFR-007/011/014, §8.4, operator stories 74–81.
