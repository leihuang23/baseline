.PHONY: dev test lint typecheck migrate fmt

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
