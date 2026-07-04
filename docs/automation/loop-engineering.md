# Baseline Task Automation Guide

Baseline tasks are already sliced into small, reviewable prompts under
`tasks/`. The fastest stable workflow is not a fully unattended implementation
loop. It is a hybrid:

1. Implement the task in the Codex App, where the agent has interactive context
   and can work efficiently.
2. Hand the resulting diff to the controller with `make task-finish`.
3. Let the controller run gates, structured review, optional focused repair,
   ledger update, and optional commit.

This keeps the speed and context of the manual workflow while preserving the
strict verification and autonomy of the loop.

## Source of Truth

- Task prompts: `tasks/P*-*.md`
- Task ledger: `tasks/ledger.json`
- Controller: `scripts/run_task_loop.py`
- Local run logs: `.task-runs/` (ignored)
- Quality gates: `make fmt`, `make lint`, `make typecheck`, `make test`

The controller advances the ledger only after the active task passes the quality
gates and the structured review gate.

`make test` runs DB-backed integration tests when Postgres is reachable. In
restricted sandboxes where local TCP to Postgres is blocked, those tests are
marked skipped and the coverage threshold is relaxed for that run. Use
`BASELINE_REQUIRE_TEST_DB=1 make test` when a task specifically needs full DB
coverage.

## Recommended Daily Workflow

Start by checking the next task:

```bash
make task-next
```

Ask Codex App to implement that task prompt. Keep the implementation scoped to
the task file and current ledger state.

When the App has produced a diff, finish and commit it through the controller:

```bash
make task-finish-commit
```

If Codex App already ran some or all quality gates, save its verification
summary to a local text file and let `finish` reuse only the gates that are
explicitly shown as passed:

```bash
python3 scripts/run_task_loop.py finish --commit --prior-verification-file .task-runs/app-verification.txt
```

For example, this evidence skips `make lint`, `make typecheck`, and `make test`,
but still runs `make fmt` because it is not mentioned:

```text
Verification:
make lint passed.
make typecheck passed.
make test passed: 210 passed, 86 skipped, 1 warning.
DB-backed tests were skipped because local Postgres was unavailable.
```

Use this path only when the evidence belongs to the current diff. If you edit
files after the App verification, rerun the affected gates or omit
`--prior-verification-file`.

To finish and commit a specific task id:

```bash
python3 scripts/run_task_loop.py finish --task P3-01 --commit
```

Use no-commit finish only when you want to inspect the completed diff and commit
manually:

```bash
make task-finish
```

The `finish` command intentionally requires an existing diff. If the tree is
clean, it stops before running gates or review. Use this escape hatch only for a
deliberate verification-only task:

```bash
python3 scripts/run_task_loop.py finish --task P3-01 --allow-no-changes
```

## What `finish` Does

`finish` never launches a broad implementation agent. It treats the current
working tree as the implementation candidate and then runs:

1. quality gates not already proven by `--prior-verification-file`
   (`make fmt`, `make lint`, `make typecheck`, `make test` by default)
2. structured Codex review scoped to the task prompt and changed files
3. one optional focused Codex repair when a gate or review returns actionable
   findings
4. focused repair verification when the failure came from structured review
5. ledger update, and optional commit

The recommended daily target is `make task-finish-commit`. It avoids leaving a
dirty tree with `tasks/ledger.json` already marked complete, which can confuse
the next task's review scope.

Prior verification is intentionally per gate, not all-or-nothing. The controller
skips a gate only when the evidence file contains the exact gate command and a
nearby pass/success term. Missing gates still run. After a final repair, all
quality gates run again because the diff has changed.

The structured review uses JSON output constrained by
`tasks/review-decision.schema.json`. A task passes only when the review decision
is `pass`.

Disable the repair pass when you want review findings handed back to the App
instead of letting the controller patch:

```bash
python3 scripts/run_task_loop.py finish --task P3-01 --no-final-repair
```

Skip the review gate only for emergency local diagnostics, not for normal task
completion:

```bash
python3 scripts/run_task_loop.py finish --task P3-01 --skip-review
```

## Fully Autonomous Lane

Use the autonomous lane when a task is low-risk, self-contained, and you are
comfortable with a cold non-interactive implementation pass.

```bash
make task-loop-one
make task-loop-one-codex
make task-loop-one-kimi
```

The default implementation agent is Codex:

```bash
python3 scripts/run_task_loop.py run --codex
```

Kimi uses non-interactive prompt mode:

```bash
python3 scripts/run_task_loop.py run --kimi
```

Automation uses a lean Codex invocation by default: no user config, ephemeral
sessions, and no terminal color. This avoids user-level MCP startup, large skill
catalogs, and persistent session artifacts inside task-loop runs. Use the full
interactive Codex config only when a task truly needs it:

```bash
python3 scripts/run_task_loop.py run --codex-full-config
python3 scripts/run_task_loop.py finish --codex-full-config
```

## Cost Controls

Implementation prompts require the agent to end with `TASK_LOOP_DONE` on its own
line. When the controller sees that exact line, it stops waiting and moves to
quality gates.

Default budgets:

- Codex implementation attempt: 3600 seconds
- Kimi implementation attempt: 1200 seconds
- structured review: 600 seconds
- focused repair: 900 seconds
- focused repair review: 300 seconds
- implementation/final-repair log limit: 2 MB
- review log limit: 1 MB

Override budgets only for tasks known to need them:

```bash
python3 scripts/run_task_loop.py run --agent-timeout-seconds 7200
python3 scripts/run_task_loop.py finish --review-timeout-seconds 1200
python3 scripts/run_task_loop.py finish --repair-review-timeout-seconds 600
python3 scripts/run_task_loop.py finish --review-log-limit-bytes 2000000
```

Use `0` to disable a timeout or log limit for a trusted long run:

```bash
python3 scripts/run_task_loop.py run --agent-timeout-seconds 0
python3 scripts/run_task_loop.py finish --review-timeout-seconds 0
```

If an autonomous implementation hits a timeout or log limit after producing a
candidate diff, the controller keeps the diff and runs gates/review. If it stops
without a candidate diff, the task blocks for inspection.

## Progress And Recovery

Inspect progress:

```bash
make task-status
```

Show the current or most recent controller state:

```bash
make task-current
make task-current-watch
```

The runner writes `.task-runs/current.json` while it works. The current view
shows task id, stage, attempt, elapsed time, timeout remaining, run directory,
log file, git status summary, and a cleaned log tail.

If a task blocks:

1. Run `make task-current`.
2. Open the run directory listed there.
3. Read the failing gate log or `review-decision.json`.
4. Fix the diff in Codex App.
5. Run `make task-finish` again.

Do not launch another broad autonomous implementation pass for a known review
or gate finding. Use the existing failure details.

## Cluster Discipline

Run one task at a time by default. Do not run future phases as one giant loop.
Finish a phase cluster, review the architecture, then move the active cluster
forward in `tasks/ledger.json`.

Whole-cluster execution remains available, but it is intentionally not the
default:

```bash
python3 scripts/run_task_loop.py run --cluster P0-foundations-finish --limit 0 --commit
```

## Decision Rule

Use `finish` for normal work. Use `run` only when the cold autonomous lane is
worth the extra token and stability risk.
