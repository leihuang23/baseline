# Baseline Loop Engineering

Baseline task slices are small enough for autonomous execution, but the loop must be bounded. The controller runs one task prompt at a time, verifies it, reviews it, updates the ledger, and only then advances.

## Source of Truth

- Task prompts: `tasks/P*-*.md`
- Task ledger: `tasks/ledger.json`
- Controller: `scripts/run_task_loop.py`
- Local run logs: `.task-runs/` (ignored)

The ledger marks `P0-01` through `P0-04` complete and starts the active cluster at `P0-foundations-finish`.

## Daily Commands

Inspect progress:

```bash
make task-status
```

Show the next task:

```bash
make task-next
```

Show the live or most recent loop state:

```bash
make task-current
```

The runner writes `.task-runs/current.json` while it works. `make task-current`
prints the active task, stage, attempt, elapsed time, timeout remaining, run
directory, log file, git change summary, and a cleaned tail of the current log.

Run exactly one task from the active cluster:

```bash
make task-loop-one
make task-loop-one-codex
make task-loop-one-kimi
```

Choose the implementation agent explicitly when needed:

```bash
python3 scripts/run_task_loop.py run --codex
python3 scripts/run_task_loop.py run --kimi
```

`--codex` is the default and runs `codex exec`. `--kimi` runs implementation attempts with Kimi Code's non-interactive `--prompt` mode. The final structured review gate still uses Codex because it depends on schema-constrained review output.

Implementation attempts are capped at 3600 seconds by default, and structured
review is capped at 1800 seconds, so a stalled agent cannot block the loop
forever. Override these per run when a task is expected to take longer:

```bash
python3 scripts/run_task_loop.py run --agent-timeout-seconds 7200
python3 scripts/run_task_loop.py run --review-timeout-seconds 3600
python3 scripts/run_task_loop.py run --agent-timeout-seconds 0 --review-timeout-seconds 0
```

Use `0` to disable a timeout for trusted long-running agents.

Long-running commands print a heartbeat every 30 seconds with elapsed time, pid,
log path, and current git change count. Override or disable the heartbeat when
needed:

```bash
python3 scripts/run_task_loop.py run --heartbeat-seconds 10
python3 scripts/run_task_loop.py run --heartbeat-seconds 0
```

Run one task and commit it after all gates pass:

```bash
make task-loop-one-commit
make task-loop-one-commit-codex
make task-loop-one-commit-kimi
```

Run the rest of the current P0 cluster:

```bash
make task-loop-p0-cluster
make task-loop-p0-cluster-codex
make task-loop-p0-cluster-kimi
```

## Quality Gates

Each completed task must pass:

```bash
make fmt
make lint
make typecheck
make test
```

The controller then runs a structured Codex review. A task is marked complete only when the gates and review pass.

## Recovery

If the loop fails, inspect the latest directory under `.task-runs/`. The runner retries once by default. If the second attempt fails, it stops without advancing the ledger.

For a run that looks stuck, check the live state first:

```bash
make task-current
tail -f "$(python3 - <<'PY'
import json, pathlib
state = json.loads(pathlib.Path('.task-runs/current.json').read_text())
print(state['log_file'])
PY
)"
```

Use a specific task when needed:

```bash
python3 scripts/run_task_loop.py run --task P0-06
```

Run a whole cluster only when you are comfortable letting Codex work for a while:

```bash
python3 scripts/run_task_loop.py run --cluster P0-foundations-finish --limit 0 --commit
```

## Operating Rule

Do not run future phases as one giant loop. Finish a phase cluster, review the architecture, then move the active cluster forward in `tasks/ledger.json`.
