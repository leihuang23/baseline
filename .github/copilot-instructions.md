# Baseline Copilot Instructions

Baseline is a private physiological decision-support system. It is an AI
engineering portfolio project, not an AI fitness coach and not a medical tool.
Keep all changes inside the wellness decision-support boundary.

## Product Invariants

- Deterministic code computes health, recovery, training, readiness, and safety
  features before any LLM call. Do not move metric computation into prompts or
  generated text.
- Use SQL/PostgreSQL for personal health and check-in data. Use retrieval only
  for curated external knowledge, never for raw personal time-series data.
- The LLM cannot invent measurements, override safety, diagnose, treat,
  prescribe dosing, or make medical claims.
- User-facing recommendations need evidence, confidence, uncertainty, and
  safety status.
- Health data is restricted. Never log raw samples, free-text notes, secrets,
  full prompt payloads, or personal health details.

## Repository Map

- `apps/api/baseline_api`: FastAPI app, domain logic, DB models,
  repositories, feature engine, reasoning, LLM orchestration, safety, privacy,
  retrieval, briefing, feedback, goals, and observability.
- `apps/api/tests`: pytest suite. DB-backed tests use the `require_db` marker
  and must run in CI with PostgreSQL available.
- `apps/ios`: thin SwiftUI client. Keep deterministic product logic in the API
  or shared fixtures/eval packages.
- `apps/dashboard`: static dependency-free internal dashboard.
- `packages/fixtures`, `packages/eval`, `packages/knowledge`: synthetic data,
  eval harness, and curated external knowledge helpers.
- `docs/architecture`, `docs/privacy`, `docs/safety`, `docs/runbooks`: product
  and engineering contracts.

The old `tasks/` implementation slices and `scripts/run_task_loop.py` are
historical automation artifacts. Do not use them as the current source of
truth for new work unless an issue explicitly asks for task-loop maintenance.

## Development Commands

Use the smallest command that proves the change, then rely on CI for the full
matrix.

- Python setup: `uv sync --all-groups`
- Format check: `uv run ruff format --check .`
- Lint: `uv run ruff check .`
- Typecheck: `make typecheck`
- Tests: `uv run pytest`
- CI-grade DB tests: `BASELINE_REQUIRE_TEST_DB=1 uv run pytest --require-db`
- Eval: `make eval`
- Docs consistency: `make docs-check`
- Dashboard tests: `npm test --prefix apps/dashboard`
- iOS tests: `swift test --package-path apps/ios`

CI runs PostgreSQL and Redis services. Locally, use
`docker compose -f infra/docker-compose.yml up -d` when DB-backed tests are
needed.

## Change Discipline

- Keep diffs small and issue-scoped.
- Prefer existing patterns and utilities. Do not add dependencies unless the
  issue explicitly requires one.
- Add or update tests for behavioral changes.
- Preserve redaction and privacy behavior whenever touching logs, metrics,
  traces, audit events, model-run records, or runbooks.
- Do not commit generated caches, local run state, secrets, `.env` files, or
  raw health data.
- If CI fails, inspect the failing job and repair that specific failure before
  broad refactoring.
