.PHONY: dev test lint typecheck migrate fmt task-status task-next task-loop-one task-loop-one-commit task-loop-p0-cluster

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

task-status:
	python3 scripts/run_task_loop.py status

task-next:
	python3 scripts/run_task_loop.py next

task-loop-one:
	python3 scripts/run_task_loop.py run

task-loop-one-commit:
	python3 scripts/run_task_loop.py run --commit

task-loop-p0-cluster:
	python3 scripts/run_task_loop.py run --cluster P0-foundations-finish --limit 0 --commit
