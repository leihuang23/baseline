UV_CACHE_DIR ?= .uv-cache
export UV_CACHE_DIR

.PHONY: dev test lint typecheck migrate fmt eval task-status task-next task-loop-one task-loop-one-codex task-loop-one-kimi task-loop-one-commit task-loop-one-commit-codex task-loop-one-commit-kimi task-loop-p0-cluster task-loop-p0-cluster-codex task-loop-p0-cluster-kimi

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

task-status:
	python3 scripts/run_task_loop.py status

task-next:
	python3 scripts/run_task_loop.py next

task-loop-one:
	python3 scripts/run_task_loop.py run

task-loop-one-codex:
	python3 scripts/run_task_loop.py run --codex

task-loop-one-kimi:
	python3 scripts/run_task_loop.py run --kimi

task-loop-one-commit:
	python3 scripts/run_task_loop.py run --commit

task-loop-one-commit-codex:
	python3 scripts/run_task_loop.py run --commit --codex

task-loop-one-commit-kimi:
	python3 scripts/run_task_loop.py run --commit --kimi

task-loop-p0-cluster:
	python3 scripts/run_task_loop.py run --cluster P0-foundations-finish --limit 0 --commit

task-loop-p0-cluster-codex:
	python3 scripts/run_task_loop.py run --cluster P0-foundations-finish --limit 0 --commit --codex

task-loop-p0-cluster-kimi:
	python3 scripts/run_task_loop.py run --cluster P0-foundations-finish --limit 0 --commit --kimi
