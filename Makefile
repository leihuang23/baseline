UV_CACHE_DIR ?= .uv-cache
DATABASE_URL ?= postgresql+psycopg://baseline@localhost:5433/baseline
export UV_CACHE_DIR
export DATABASE_URL

.PHONY: dev test lint typecheck migrate fmt eval demo

dev:
	uv run uvicorn baseline_api.main:app --reload

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy

migrate:
	uv run alembic upgrade head

fmt:
	uv run ruff format .
	uv run ruff check --fix .

eval:
	uv run python -m packages.eval

demo:
	uv run python -m packages.eval.demo
