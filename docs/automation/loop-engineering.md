# Baseline Task Automation Guide

Baseline tasks are already sliced into small, reviewable prompts under
`tasks/`. The controller supports two stable lanes:

1. The fully autonomous lane, which mirrors the manual App workflow: task prompt
   implementation, generated review/audit prompt pack, focused repair, and
   repeat verification until every gate is green or the repair budget is
   exhausted.
2. The hybrid finish lane, where Codex App produces the implementation diff and
   the controller runs the same gates, review, audit, repair, ledger update, and
   optional commit.

Autonomous runs use the full Codex user/project config by default so CLI agents
have the same local capabilities as the App. Use `--codex-lean` only for simple
tasks where avoiding user-level MCP/hooks startup matters more than capability.

## Source of Truth

- Task prompts: `tasks/P*-*.md`
- Task ledger: `tasks/ledger.json`
- Controller: `scripts/run_task_loop.py`
- Generated prompt schema: `tasks/prompt-pack.schema.json`
- Local run logs and generated prompt snapshots: `.task-runs/` (ignored)
- Quality gates: `make fmt`, `make lint`, `make typecheck`, `make test`

The controller advances the ledger only after the active task passes quality
gates, generated Codex review, generated Codex audit, and any generated extra
audits.

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
the task file and current ledger state, or run the whole task through Codex CLI:

```bash
make task-loop-one
```

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

To restart an interrupted autonomous run with an unfinished dirty diff, use the
same continuous command again:

```bash
python3 scripts/run_task_loop.py run --limit 0 --commit
```

When the worktree is dirty, `run` reads `.task-runs/current.json`. If that state
file points at a non-complete pending task, the controller first routes the
current diff through the cheapest safe resume path for that task. When the last
recorded audit stage already succeeded, all quality gates are proven in
`run-summary.json`, and the current product diff still matches the reviewed
scope, the controller fast-forwards directly to ledger update and commit. When
that evidence is missing or stale, it falls back to the full `finish` lane:
quality gates, generated review/audit, focused repair, ledger update, and
commit. After the commit leaves the tree clean, it continues to the next pending
tasks. If the dirty diff cannot be tied to one unfinished task-loop run, the
controller still blocks instead of guessing.

Use no-commit finish only when you want to inspect the completed diff and commit
manually:

```bash
make task-finish
```

The `finish` command intentionally requires an existing diff. If the tree is
clean, it stops before running gates, review, or audit. Use this escape hatch only for a
deliberate verification-only task:

```bash
python3 scripts/run_task_loop.py finish --task P3-01 --allow-no-changes
```

## What `finish` Does

`finish` never launches a broad implementation agent. It treats the current
working tree as the implementation candidate and then runs:

1. quality gates not already proven by `--prior-verification-file`
   (`make fmt`, `make lint`, `make typecheck`, `make test` by default)
2. generated prompt-pack creation from the implementation prompt and changed
   files
3. generated Codex review scoped to the task prompt and changed files
4. generated Codex audit scoped to acceptance, verification adequacy, privacy,
   safety, and task-specific risks
5. any generated extra audits, such as UI state-machine or contract checks
6. bounded focused Codex repair when a gate, review, or audit returns actionable
   blocker/major findings
7. repeat gates plus focused review/audit verification until green or the final
   repair budget is exhausted
8. ledger update, optional commit, and auto-pause when human verification is
   sensible before the next task

The recommended daily target is `make task-finish-commit`. It avoids leaving a
dirty tree with `tasks/ledger.json` already marked complete, which can confuse
the next task's review scope.

Prior verification is intentionally per gate, not all-or-nothing. The controller
skips a gate only when the evidence file contains the exact gate command and a
nearby pass/success term. Missing gates still run. After a final repair, all
quality gates run again because the diff has changed.

The prompt-pack generator uses JSON output constrained by
`tasks/prompt-pack.schema.json`. It writes the exact generated prompts into the
run directory as `generated-review-prompt.md`, `generated-audit-prompt.md`, and
`extra-audit-*-prompt.md` files. Review and audit decisions use JSON output
constrained by `tasks/review-decision.schema.json`.

The generated audit prompt is created from the implementation prompt plus the
changed-file snapshot. It should always check task acceptance, verification
adequacy, integration drift, and privacy/safety risk. It can add focused extra
audits for likely UI state machines, API/schema/migration contracts,
auth/permission boundaries, data lifecycle work, reasoning/safety paths, and
eval or golden-scenario changes.

Focused repair reuses the existing generated prompt pack when the changed-file
set has not expanded. If a repair adds a new file, the controller regenerates
the prompt pack so review and audit scope stays current. Pure repair-audit
verification uses the prior audit finding directly and does not regenerate a
full review/audit pack.

Disable the repair pass when you want review findings handed back to the App
instead of letting the controller patch:

```bash
python3 scripts/run_task_loop.py finish --task P3-01 --no-final-repair
```

Skip review or audit gates only for emergency local diagnostics, not for normal
task completion:

```bash
python3 scripts/run_task_loop.py finish --task P3-01 --skip-review
python3 scripts/run_task_loop.py finish --task P3-01 --skip-audit
```

## Fully Autonomous Lane

Use the autonomous lane when you want Codex CLI to run the same implementation,
review, audit, and focused repair loop that you were doing manually in the App.
Run one task at a time by default.

```bash
make task-loop-one
make task-loop-one-codex
```

Codex is the only implementation agent. The `--codex` flag is kept as a
backward-compatible no-op:

```bash
python3 scripts/run_task_loop.py run --codex
```

Automation loads the full Codex user/project config by default so CLI runs can
use the same local tools, hooks, skills, and subagent surfaces available in the
Codex App. Use lean mode for low-risk tasks when startup cost matters more than
capability:

```bash
python3 scripts/run_task_loop.py run --codex-lean
python3 scripts/run_task_loop.py finish --codex-lean
```

## Cost Controls

Implementation prompts require the agent to end with `TASK_LOOP_DONE` on its own
line. When the controller sees that exact line, it stops waiting and moves to
quality gates. Run directories for the first edit pass are named
`...-implementation`; focused post-gate/review/audit repairs remain named
`...-final-repair-N`.

Default budgets:

- Codex implementation pass: 3600 seconds
- prompt-pack generation/review/audit: 600 seconds
- focused repair: 900 seconds
- focused repair review: 300 seconds
- final repair attempts: 2 per actionable failure class (`gate` and `decision`)
- implementation/final-repair log limit: disabled by default
- review/audit log limit: disabled by default

Override budgets only for tasks known to need them:

```bash
python3 scripts/run_task_loop.py run --agent-timeout-seconds 7200
python3 scripts/run_task_loop.py finish --review-timeout-seconds 1200
python3 scripts/run_task_loop.py finish --repair-review-timeout-seconds 600
python3 scripts/run_task_loop.py finish --final-repair-attempts 3
python3 scripts/run_task_loop.py finish --review-log-limit-bytes 2000000
```

Use `0` to disable a timeout or log limit:

```bash
python3 scripts/run_task_loop.py run --agent-timeout-seconds 0
python3 scripts/run_task_loop.py finish --review-timeout-seconds 0
```

Timeouts remain the default cost brake for runaway Codex sessions. Log limits
are opt-in because large but productive diffs can produce verbose logs; killing
the agent for log volume tends to create partial diffs after tokens have already
been spent. If an autonomous implementation hits a timeout or an explicit log
limit after producing a candidate diff, the controller keeps the diff and runs
gates, review, and audit. If it stops without a candidate diff, the task blocks
for inspection.

Implementation and repair prompts tell agents to run only targeted checks
inside the agent pass. Prefer `pytest --no-cov` for focused repair tests; the
controller owns full `make test` coverage gates.

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
shows task id, stage, implementation pass or focused repair count, elapsed time,
timeout remaining, run directory, log file, prompt file, git status summary,
and a cleaned log tail. Each run
directory keeps the exact implementation, review, audit, and repair prompts that
were sent to Codex. It also writes `run-summary.json`, a compact machine-readable
stage list with elapsed seconds, log sizes, exit codes, and token counts when
Codex reports them. Successful terminal stages also record worktree fingerprints
so an interrupted resume can reuse a prior passing audit only when the current
product diff still matches the audited content. Older run states that predate
fingerprints can use a path-scope match once; new runs use the stronger
fingerprint check.

If a task blocks:

1. Run `make task-current`.
2. Open the run directory listed there.
3. Read the failing gate log, `review-decision.json`, or `audit-decision.json`.
4. Fix the diff in Codex App.
5. Run `make task-finish` again.

Do not launch another broad autonomous implementation pass for a known review,
audit, or gate finding. Use the existing failure details.

## Protected Controller Files

Normal task commits are not allowed to include task-loop controller files:

- `scripts/run_task_loop.py`
- `apps/api/tests/test_task_loop.py`
- `docs/automation/`
- task-loop prompt/decision schemas

If a product task leaves tracked modifications in those files, the controller
restores them before marking the ledger complete. This protects the loop from
mutating gates such as `make fmt` touching controller files while a product task
is in progress. New, copied, or renamed protected files still block completion,
because deleting unknown files automatically would be too aggressive. Commit or
remove those files separately, or use an explicit automation task whose
prompt/title is about the task loop, controller, prompt pack, or automation.

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

Use `run` when you want the controller to own the full manual-style loop. Use
`finish` when the App or another agent already produced the candidate diff and
you only need the controller's gates, review, audit, repair, ledger update, and
optional commit.
