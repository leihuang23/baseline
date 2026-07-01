# P5-03: Evaluation & operations dashboard

**Phase:** 5 — Knowledge retrieval & eval dashboard (V1) | **Depends on:** P0-07, P3-06, P0-06 | **Parallelizable with:** P5-01/02 | **Surface:** dashboard (`apps/dashboard`)

## Context (self-contained)
The internal dashboard is **production infrastructure, not a nice-to-have** (§27.8) and a core portfolio artifact — it's what a hiring manager inspects. It visualizes pipeline health, recommendation traces, LLM runs, cost/latency, eval results, and safety events, and supports an **anonymized demo mode** so it can be shown publicly without exposing private data.

## Goal
Build the internal dashboard surfacing sync/pipeline health, data completeness, recommendation traces, LLM runs, cost/latency, eval results, and safety events — with an anonymized portfolio demo mode.

## Scope
In (§24.2 + §12.12):
- Pipeline health: sync success/latency, feature-job status, LLM-generation status, recent failed jobs + retry status (FR-092).
- Data completeness by day; recommendation **traces** browser (FR-093) reading the trace ids from P3-06.
- LLM runs (from `ModelRun`): model, prompt version, tokens, cost, latency, safety result; cost + latency charts (§23.1).
- Eval results from the harness (P0-07): pass/fail by suite/type over time; **safety policy violations** caught by tests/evals (FR-094).
- **Anonymized demo mode** using synthetic data only — no private data (FR-095, §27.10).
- Read-only; served from `apps/dashboard` (lightweight web UI); auth-gated for the operator.

Out:
- Alerting/budgets + fallback (P5-04); the eval harness itself (P0-07).

## Deliverables
- `apps/dashboard/` app reading eval results, ModelRun, traces, and pipeline metrics.

## Acceptance criteria
- [ ] All §24.2 sections render from real (or synthetic) data.
- [ ] Recommendation traces are browsable; LLM runs show cost/latency/safety; eval + safety-violation views work.
- [ ] Demo mode shows a compelling anonymized walkthrough with zero private data (leak-tested).
- [ ] No raw health data / notes / secrets rendered anywhere (redaction respected).

## Tests required
- Rendering/integration tests per section against synthetic data.
- Demo-mode private-data-leak test; redaction-in-dashboard test.

## PRD references
§12.12 FR-091–095, §24.2 Internal Dashboard, §23.1 metrics, §27.8/10.
