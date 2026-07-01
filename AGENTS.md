# Baseline Agent Guide

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
---

Baseline is a private physiological decision-support system. It ingests Apple Health and manual lifestyle data, computes deterministic features, retrieves structured personal evidence, and produces evidence-backed daily training/recovery briefings.

The product is not an "AI fitness coach" and not a medical tool. Treat it as a production-oriented AI engineering portfolio project for a privacy-sensitive health-data system.

## Source Of Truth

- Product requirements: `personal-physiological-os-prd.md`
- Agent task slices: `tasks/README.md` and `tasks/P*-*.md`
- App name: `Baseline`

## Current Scaffold

- Python package lives in `apps/api/baseline_api`.
- API entry point is `baseline_api.main:app`; use `create_app()` in tests.
- Versioned routes belong under `apps/api/baseline_api/api/v1`.
- Local infrastructure lives in `infra/docker-compose.yml`.
- Alembic scaffolding exists under `apps/api/alembic`; P0-02 owns the first real migration.

## Backend Guardrails

- Configuration is loaded with `pydantic-settings` from environment variables only.
- Required runtime variables must be documented in `.env.example`.
- Never add real secrets, raw health data, free-text notes, or prompt payloads to source,
  tests, logs, fixtures, or documentation.
- Keep `/health` and `/v1/health/ping` dependency-light; they should prove the service boots,
  not touch Postgres, Redis, or domain state.
- Do not add domain models, business logic, auth, or non-health endpoints in foundation slices.

## Verification Commands

- `make lint` runs ruff checks.
- `make typecheck` runs mypy strict checks for `baseline_api`.
- `make test` runs pytest with coverage and fails under 80%.
- `make fmt` applies ruff formatting and safe lint fixes.

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) for all commit messages. Use structured prefixes (`feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:`) with an optional scope, and include a concise description in the imperative mood. Append a body when the change needs additional context (e.g., breaking changes, motivation, or side effects). Do not commit with vague or single-word messages.
