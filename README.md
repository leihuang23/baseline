# Baseline

Baseline is a private physiological decision-support system. It ingests Apple Health and
manual lifestyle data, computes deterministic features, retrieves structured personal evidence,
and produces evidence-backed daily training/recovery briefings.

## Local setup in 3 commands

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d
uv run uvicorn baseline_api.main:app --reload
```

Then open `http://127.0.0.1:8000/health`. It should return HTTP 200.

Useful checks:

```bash
make lint
make typecheck
make test
```

`make test` uses Postgres integration tests when the configured database is
reachable. In restricted sandboxes where local TCP to Postgres is blocked, those
DB-marked tests are skipped and the coverage threshold is relaxed for that run
so environment policy does not hide non-DB test results. To require the database
and fail fast when it is unavailable:

```bash
BASELINE_REQUIRE_TEST_DB=1 make test
uv run pytest --require-db
```
