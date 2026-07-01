# Baseline — Task Slices

Agent-ready task breakdown for **Baseline** (working title: *Personal Physiological Operating System*), derived from [`personal-physiological-os-prd.md`](../personal-physiological-os-prd.md).

Each file in this directory is a **self-contained prompt** you can paste into a coding agent (Claude Code / Codex). Every prompt states scope, goals, deliverables, acceptance criteria, tests, and dependencies. Work them in dependency order; parallelizable slices are marked.

---

## The one rule that shapes everything

> **Feature extraction → deterministic reasoning → evidence retrieval → LLM explanation → safety validation → user feedback → evaluation loop.**

The LLM is *one component*, not the brain. Non-negotiable architectural invariants (repeat these to any agent that drifts):

1. **Deterministic first.** All health/training features and readiness logic are computed in plain, versioned, testable code. The LLM never computes a metric.
2. **SQL for me, RAG for the world.** Personal time-series data is retrieved with SQL/time-series queries. Vector RAG is *only* for a curated external-knowledge corpus.
3. **The LLM cannot invent data or override safety.** No fabricated measurements; hard safety flags are enforced *after* generation and cannot be overridden.
4. **Every user-facing recommendation carries evidence, confidence, uncertainty, and a safety status.** No exceptions.
5. **Health data is restricted data.** Minimize before any external call; never log raw samples, notes, secrets, or prompts containing personal data.
6. **Wellness decision-support, not medical advice.** No diagnosis, treatment, or dosing — ever.

---

## Target stack (baked into every backend prompt)

- **Language/runtime:** Python 3.12+, managed with **uv** (preferred) or Poetry.
- **API:** FastAPI + Uvicorn. Versioned routes under `/v1`.
- **DB/ORM:** PostgreSQL 16 + **SQLModel** (SQLAlchemy 2.0) + **Alembic** migrations. Optional **DuckDB** for offline eval/analytics.
- **Schemas/validation:** Pydantic v2 everywhere (API contracts §17, recommendation contract §18, LLM structured outputs).
- **Async jobs:** **arq** + Redis (preferred) or Celery. Idempotent, retryable jobs.
- **LLM layer:** provider-agnostic interface; default **Anthropic Claude** — `claude-haiku-4-5` for cheap classify/summarize, `claude-opus-4-8` for complex longitudinal/planning. Pluggable (OpenAI etc.). Structured output via Pydantic JSON schema.
- **Tests:** pytest, pytest-asyncio, **hypothesis** (property tests), respx/VCR (HTTP mocking), coverage ≥ 80%.
- **Quality:** ruff (lint+format), mypy (strict on core modules), pre-commit.
- **Observability:** structlog with a redaction processor, OpenTelemetry traces, Prometheus metrics.
- **iOS (thin):** SwiftUI, HealthKit, Keychain + Data Protection, async/await, URLSession.

## Repository layout (agents should conform to this)

```
baseline/
  apps/
    api/           baseline_api/{config,db,schemas,ingestion,features,reasoning,
                   llm,safety,memory,retrieval,briefing,feedback,observability,
                   privacy,api}/  + tests/  + alembic/
    ios/           SwiftUI app (thin: auth+sync, check-in, briefing)
    dashboard/     internal eval/ops dashboard (Phase 5)
  packages/
    fixtures/      synthetic data generators + golden fixtures
    eval/          shared evaluation harness
    knowledge/     curated external corpus (Phase 5)
  docs/            architecture/ · safety/ · privacy/
  infra/           docker-compose.yml, CI
  pyproject.toml · Makefile · .github/workflows/
```

## Global conventions (every prompt inherits these)

- Immutable updates; no in-place mutation. No magic values — extract to named constants/enums.
- Files ≤ ~400 lines (hard cap 800); functions ≤ ~50 lines; nesting ≤ 4.
- Explicit error contracts; never swallow errors. Validate all inputs at boundaries (Pydantic).
- Conventional Commits. Every feature ships with tests; deterministic modules use golden fixtures.
- Versioned artifacts: feature formulas, prompts, schemas, and assessments all carry a version.

---

## Phase map & dependency graph

| Phase | Theme | Slices |
|-------|-------|--------|
| 0 | Feasibility & foundations | P0-01 … P0-07 |
| 1 | Data ingestion MVP | P1-01 … P1-04 |
| 2 | Feature engine & check-in | P2-01 … P2-05 |
| 3 | Reasoning, briefing, safety (core loop) | P3-01 … P3-08 |
| 4 | Memory & feedback loop | P4-01 … P4-04 |
| 5 | Knowledge retrieval & eval dashboard | P5-01 … P5-04 |
| 6 | Portfolio packaging | P6-01 … P6-02 |

```
P0-01 scaffold ─┬─ P0-02 data model ─┬─ P0-03 fixtures
                │                     ├─ P0-04 API/schema contracts
                │                     ├─ P0-06 observability/redaction
                │                     └─ P0-07 eval harness scaffold
                └─ P0-05 safety+privacy design (parallel, docs)

P0-02,04,06 → P1-01 sync API → P1-02 normalization → P1-03 backfill/quality
                                     └────────────→ P1-04 iOS auth+sync (needs P1-01)

P1-02 → P2-02 features(sleep/cardio) ─┐
P2-01 check-in API ───────────────────┼→ P2-03 features(load/density) → P2-04 feature golden tests
                                       └→ P2-05 iOS check-in+goals UI (needs P2-01, P3-01)

P2-04 + P3-01 goals → P3-02 reasoning engine → P3-03 reasoning scenarios
P0-04 → P3-04 LLM orchestrator ; P0-05 → P3-05 safety engine
P3-02,04,05 → P3-06 briefing assembly+APIs → P3-07 assistant Q&A
P3-06 → P3-08 iOS briefing+trace UI

P3-06 → P4-01 memory(daily/weekly) → P4-02 memory(monthly/quarterly)
P3-06 → P4-03 feedback loop ; (all core entities) → P4-04 data controls

P4-* → P5-01 knowledge corpus → P5-02 retrieval+citations
P0-06,07 + P3-* → P5-03 eval/ops dashboard ; P3-04 → P5-04 cost/fallback/degraded

P0-03 + P3-06 → P6-01 demo mode+leak tests → P6-02 portfolio docs
```

**Parallel-friendly clusters:** P0-05 alongside all of P0; P1-04 (iOS) alongside P1-02/03; P3-04 & P3-05 alongside P3-02/03; P5-01/02 alongside P5-03/04.

## MVP definition of done (§22.3)

HealthKit authorize + sync (sleep, workouts, steps, HRV, RHR, VO2 where available) → morning check-in → deterministic daily briefing with **evidence + confidence + uncertainty + safety status** → handles missing data without hallucination → ≥30 golden scenarios pass → medical prompts refused/redirected → logs redacted → export & delete work → synthetic demo mode works. Slices P0–P3 + P4-04 + P6-01 cover this.
