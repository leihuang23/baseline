# Baseline Agent Guide

Baseline is a private physiological decision-support system. It ingests Apple
Health and manual lifestyle data, computes deterministic features, retrieves
structured evidence, and produces evidence-backed daily training/recovery
briefings.

The product is not an "AI fitness coach" and not a medical tool. Treat it as a
production-oriented AI engineering portfolio project for a privacy-sensitive
health-data system.

## Operating Rules

### Think Before Coding

Do not assume. Do not hide confusion. Surface tradeoffs.

- State assumptions explicitly when they matter.
- If multiple interpretations exist, present them instead of silently choosing.
- If a simpler approach exists, say so.
- If the objective is unclear, stop and ask.

### Simplicity First

Minimum code that solves the requested problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No configurability that was not requested.
- Prefer deletion, existing utilities, and existing patterns before new code.
- If a change can be smaller without losing correctness, make it smaller.

### Surgical Changes

Touch only what the request requires.

- Match existing style, even if you would choose differently.
- Do not refactor adjacent code unless it is necessary for the task.
- Remove imports, variables, functions, and files made unused by your change.
- Do not remove pre-existing dead code unless asked; mention it instead.
- Never overwrite or revert unrelated user changes in the worktree.

### Goal-Driven Execution

Turn work into verifiable goals.

For multi-step work, start with a short plan:

```text
1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]
```

Then loop until the success criteria are met or a real blocker remains.

## Source Of Truth

- Product requirements: `personal-physiological-os-prd.md`
- Task slices: `tasks/README.md` and `tasks/P*-*.md`
- Task status and active cluster: `tasks/ledger.json`
- Loop automation guide: `docs/automation/loop-engineering.md`
- App name: `Baseline`

## Current Project Shape

### API

- Python package: `apps/api/baseline_api`
- App factory: `baseline_api.app:create_app`
- ASGI entry point: `baseline_api.main:app`
- Runtime config: `baseline_api.config.Settings`, loaded from environment via
  `pydantic-settings`
- API routers currently live in `apps/api/baseline_api/api`:
  health, assistant, check-ins, data controls, goals, `/v1/health`,
  `/v1/contracts`, and `/v1/observability`
- Database layer:
  `db/models`, `db/repositories`, `db/session.py`, and Alembic migrations under
  `apps/api/alembic/versions`
- Core domain modules:
  `ingestion`, `features`, `reasoning`, `llm`, `safety`, `memory`, `retrieval`,
  `briefing`, `feedback`, `goals`, `privacy`, `observability`, `schemas`,
  `checkin`, and `assistant`

### iOS

- Swift package: `apps/ios`
- Thin SwiftUI client for onboarding, privacy mode selection, HealthKit sync,
  daily check-ins, goals, briefing display, and trace UI
- Keep the iOS app thin: auth, permissions, sync, presentation, and local
  persistence belong here; deterministic product logic belongs in the API or
  shared fixtures/eval packages.

### Dashboard

- Static, dependency-free internal dashboard: `apps/dashboard`
- Demo mode can open `index.html` directly.
- Real operator mode is read-only and host-gated through
  `window.BASELINE_DASHBOARD_AUTH` plus sanitized
  `window.BASELINE_DASHBOARD_DATA`.

### Shared Packages

- `packages/fixtures`: synthetic data models, generators, loaders, and scenarios
- `packages/eval`: evaluation definitions, suites, scorers, reporters, CLI, and
  golden scenario adapters
- `packages/knowledge`: curated external knowledge corpus, chunking, curation,
  embeddings, and store helpers

### Docs And Infrastructure

- Architecture docs: `docs/architecture`
- Safety docs and policy: `docs/safety`
- Privacy docs and data flow: `docs/privacy`
- Runbooks: `docs/runbooks`
- Local infrastructure: `infra/docker-compose.yml`

## Architectural Invariants

- Deterministic first. Health/training features and readiness logic are plain,
  versioned, testable code. The LLM never computes metrics.
- SQL for personal data; curated knowledge retrieval for external evidence.
  Vector/RAG-style retrieval is for the external knowledge corpus, not raw
  personal time-series data.
- The LLM cannot invent data or override safety. Safety validation happens
  after generation and hard safety flags win.
- Every user-facing recommendation needs evidence, confidence, uncertainty, and
  safety status.
- Health data is restricted data. Minimize before external calls; never log raw
  samples, notes, secrets, prompt payloads, or personal health details.
- Wellness decision support only. No diagnosis, treatment, dosing, or medical
  claims.

## Backend Guardrails

- Required runtime variables must be documented in `.env.example`.
- Keep configuration environment-only; do not introduce checked-in secrets or
  local config files with private values.
- Keep `/health` and `/v1/health/ping` dependency-light. They prove the service
  boots and must not touch Postgres, Redis, or domain state.
- Use Pydantic/SQLModel schemas at boundaries. Do not pass unvalidated dicts
  through core logic when a typed model already exists.
- Preserve redaction behavior in `observability` and privacy modules whenever
  adding logs, metrics, traces, audit events, model-run records, or runbooks.
- Version artifacts that define behavior: feature formulas, schemas, prompts,
  assessments, eval suites, and migrations.

## Task Loop Engineering

The loop controller is `scripts/run_task_loop.py`. Use it when working through
the agent task slices in `tasks/`.

Primary references:

- Guide: `docs/automation/loop-engineering.md`
- Ledger: `tasks/ledger.json`
- Prompt schema: `tasks/prompt-pack.schema.json`
- Review decision schema: `tasks/review-decision.schema.json`
- Local run state and logs: `.task-runs/` (ignored)

Useful commands:

```bash
make task-status
make task-next
make task-current
make task-current-watch
make task-loop-one
make task-finish
make task-finish-commit
```

Decision rule:

- Use `make task-loop-one` when the controller should own implementation,
  quality gates, generated review/audit, focused repair, and ledger update.
- Use `make task-finish` when Codex App or another agent has already produced a
  candidate diff and the controller should verify, review, audit, repair, and
  update the ledger.
- Use `make task-finish-commit` when the candidate diff should be verified,
  repaired if needed, marked complete in the ledger, and committed.
- Do not launch another broad autonomous implementation pass for a known gate,
  review, or audit finding. Read `make task-current`, inspect the failing log or
  decision JSON, then repair the existing diff.

Loop rules:

- Autonomous implementation prompts must end with `TASK_LOOP_DONE` on its own
  line so the controller can stop waiting and run gates.
- The controller owns full gates: `make fmt`, `make lint`, `make typecheck`,
  and `make test`.
- Generated review and audit are normal completion gates. Skip them only for
  emergency local diagnostics.
- The controller advances `tasks/ledger.json` only after quality gates,
  generated review, generated audit, any extra audits, and bounded focused
  repairs are green.
- Protected controller files are not part of normal product task commits:
  `scripts/run_task_loop.py`, `apps/api/tests/test_task_loop.py`,
  `docs/automation/`, and the task-loop schemas. Change them only for explicit
  loop-engineering work.
- Run one task at a time by default. Use cluster execution only deliberately,
  then review the architecture before moving to the next phase.

## Verification Commands

Backend:

```bash
make fmt
make lint
make typecheck
make test
make eval
```

`make test` uses Postgres integration tests when the configured database is
reachable. In restricted sandboxes where local TCP to Postgres is blocked,
DB-marked tests are skipped and coverage is relaxed for that run. To require DB
coverage:

```bash
BASELINE_REQUIRE_TEST_DB=1 make test
uv run pytest --require-db
```

iOS:

```bash
swift test --package-path apps/ios
```

Dashboard:

```bash
npm test --prefix apps/dashboard
```

Choose the smallest verification that proves the claim, then broaden when the
change touches shared behavior, contracts, privacy/safety paths, migrations,
evals, or user-facing flows.

## Commit Messages

Use Conventional Commit prefixes (`feat:`, `fix:`, `docs:`, `style:`,
`refactor:`, `test:`, `chore:`) with a concise imperative description. Add a
body when the change needs motivation, side effects, or verification detail. Do
not commit vague one-word messages.
