# Baseline Loop Engineering

Baseline task slices are small enough for autonomous execution, but the loop must be bounded. The controller runs one task prompt at a time, verifies it, reviews it, optionally performs one focused repair, updates the ledger, and only then advances.

## Source of Truth

- Task prompts: `tasks/P*-*.md`
- Task ledger: `tasks/ledger.json`
- Controller: `scripts/run_task_loop.py`
- Local run logs: `.task-runs/` (ignored)

The ledger marks `P0-01` through `P0-04` complete and starts the active cluster at `P0-foundations-finish`.
Task selection is dependency-aware. Dependencies are read from each task prompt's
`Depends on` header, so the loop skips pending tasks whose prerequisites are not
complete and advances to the first runnable task that unlocks the graph. For
example, `P2-05` is not runnable until `P3-01` is complete.

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
make task-current-watch
```

The runner writes `.task-runs/current.json` while it works. `make task-current`
prints the active task, stage, attempt, elapsed time, timeout remaining, run
directory, log file, git change summary, and a cleaned tail of the current log.
Use `make task-current-watch` to refresh that view continuously until Ctrl-C.

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

`--codex` is the default and runs `codex exec`. Automation uses a lean Codex
invocation by default: no user config, ephemeral sessions, and no terminal
color. This keeps user-level MCP startup, large skill catalogs, and persistent
session artifacts out of task-loop runs. Use `--codex-full-config` only when a
task truly needs the full interactive Codex profile. `--kimi` runs
implementation attempts with Kimi Code's non-interactive `--prompt` mode. The
final structured review gate still uses Codex because it depends on
schema-constrained review output.

Implementation attempts are capped at 3600 seconds by default, structured review
is capped at 600 seconds, and the focused post-repair verification review is
capped at 300 seconds, so a stalled agent cannot block the loop forever. The
default run performs one implementation attempt, then at most one focused Codex
repair pass for concrete gate or review findings. Override budgets only when a
task is expected to need them:

```bash
python3 scripts/run_task_loop.py run --agent-timeout-seconds 7200
python3 scripts/run_task_loop.py run --review-timeout-seconds 3600
python3 scripts/run_task_loop.py run --repair-review-timeout-seconds 600
python3 scripts/run_task_loop.py run --agent-timeout-seconds 0 --review-timeout-seconds 0
```

Use `0` to disable a timeout for trusted long-running agents.

## Cost Controls

Implementation prompts now require the agent to end the final response with
`TASK_LOOP_DONE` on its own line. When the controller sees that marker in the
log, it stops waiting and moves directly to quality gates. This handles the
common failure mode where an agent has already summarized completed work but the
CLI process keeps running until the timeout.

The runner also applies log-size budgets:

```bash
python3 scripts/run_task_loop.py run --agent-log-limit-bytes 2000000
python3 scripts/run_task_loop.py run --review-log-limit-bytes 1000000
python3 scripts/run_task_loop.py run --agent-log-limit-bytes 0 --review-log-limit-bytes 0
```

If an implementation hits the timeout or log budget after producing a candidate
diff, the controller records the budget stop and still runs its own gates and
review. If the budget stop produces no candidate diff, the task blocks for
inspection. A clean implementation pass that exits without any candidate diff
also blocks before gates/review; rerun with `--allow-no-changes` only for an
intentional verification-only task.

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

The controller then runs a structured Codex review. That review is a static
diff review scoped to the task prompt and changed files; it should not rerun
builds or tests after the controller gates have already run. A task is marked
complete only when the gates and review pass.

If a gate fails or the structured review returns concrete findings, the
controller runs one focused Codex repair pass against those details, then reruns
the gates. If the failure came from structured review findings, the post-repair
review verifies only that the original findings were resolved and that the repair
did not introduce an obvious direct blocker or major regression. It is not a
second full review that can keep moving the goalposts. If that repair
verification fails, or if the review command times out, is interrupted, or cannot
produce valid JSON, the task is blocked for inspection instead of launching
another broad implementation pass.

## Recovery

If the loop fails, inspect the latest directory under `.task-runs/`. The runner
does not retry broad implementation by default. It either completes after the
initial pass, completes after one focused repair, or stops without advancing the
ledger.

For a run that looks stuck, check the live state first:

```bash
make task-current-watch
```

Tune the live view when needed:

```bash
python3 scripts/run_task_loop.py current --watch --interval 1
python3 scripts/run_task_loop.py current --watch --tail-lines 80
```

Use a specific task when needed:

```bash
python3 scripts/run_task_loop.py run --task P0-06
```

Explicit task runs still enforce dependencies. If the named task is waiting on
another slice, the runner exits before launching an implementation agent and
prints the unmet dependency list.

Run a whole cluster only when you are comfortable letting Codex work for a while:

```bash
python3 scripts/run_task_loop.py run --cluster P0-foundations-finish --limit 0 --commit
```

## Operating Rule

Do not run future phases as one giant loop. Finish a phase cluster, review the architecture, then move the active cluster forward in `tasks/ledger.json`.
