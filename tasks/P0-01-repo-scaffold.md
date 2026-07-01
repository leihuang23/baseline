# P0-01: Monorepo scaffold, tooling & CI

**Phase:** 0 — Feasibility & foundations | **Depends on:** none | **Parallelizable with:** P0-05 | **Surface:** backend/infra

## Context (self-contained)
You are bootstrapping **Baseline**, a private "personal physiological operating system": it ingests Apple Health + manual check-ins and produces evidence-backed daily training/recovery briefings. Backend is **Python 3.12 + FastAPI + PostgreSQL + SQLModel/Alembic**, jobs via **arq + Redis**, LLM layer provider-agnostic (default DeepSeek V4 Pro). It is an AI-engineering portfolio project, so engineering hygiene is a deliverable, not an afterthought.

## Goal
Create the monorepo skeleton, dependency management, local dev environment, and CI so every later slice starts from a green, reproducible baseline.

## Scope
In:
- Repo layout per `tasks/README.md` (`apps/api`, `apps/ios` placeholder, `apps/dashboard` placeholder, `packages/{fixtures,eval,knowledge}`, `docs/`, `infra/`).
- `pyproject.toml` managed by **uv**; FastAPI app that boots with a `/health` and `/v1/health/ping` endpoint.
- `apps/api/baseline_api/config.py`: pydantic-settings config loaded from env; **no secrets in code**; `.env.example` documenting every var.
- `infra/docker-compose.yml`: Postgres 16 + Redis; `Makefile` targets (`dev`, `test`, `lint`, `typecheck`, `migrate`, `fmt`).
- Tooling: ruff (lint+format), mypy (strict for `baseline_api/**`), pre-commit hooks, pytest + coverage config (fail under 80%).
- `.github/workflows/ci.yml`: install → lint → typecheck → test on push/PR.

Out (do NOT do here):
- Any domain models, business logic, or migrations (that is P0-02).
- Auth, real endpoints beyond the ping/health stubs.

## Deliverables
- Bootable FastAPI app, `docker-compose up` brings up Postgres+Redis, `make test`/`make lint`/`make typecheck` all green on an empty test.
- README section: "Local setup in 3 commands".
- add more details to AGENTS.md.

## Acceptance criteria
- [ ] Fresh clone → documented commands → server responds 200 on `/health`.
- [ ] CI passes on a trivial test; coverage gate active.
- [ ] No secret literals anywhere; config fails fast with a clear error if a required var is missing.
- [ ] ruff + mypy(strict) clean.

## Tests required
- Smoke test hitting `/health` and `/v1/health/ping`.
- Config test: missing required env → startup raises a clear, typed error.

## PRD references
§16 Architecture, §26 Implementation Decisions, NFR-004 (no secrets in source).
