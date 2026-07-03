# Kimi task loop design

Baseline keeps a Kimi-specific task loop instead of treating Kimi Code as a
drop-in Codex flag. The goal is not to make Kimi do less work; it is to keep
Kimi's long-context, agentic strengths pointed at the smallest useful slice.

## Sources

- Kimi Code CLI docs: <https://moonshotai.github.io/kimi-code/>
- Kimi Code CLI local help: `kimi --help`
- Kimi K2 technical report: <https://arxiv.org/abs/2507.20534>

The CLI docs describe Kimi Code as a tool for long focused agent sessions with
skills, hooks, sub-agents, and MCP extension points. The local CLI exposes prompt
mode separately from permission modes such as `--yolo` and `--auto`, so the loop
must not combine incompatible prompt-mode flags. The K2 technical report
emphasizes agentic software-engineering capabilities and long-context use, which
is useful for broad code tasks but also benefits from explicit controller
boundaries.

## Controller policy

Kimi defaults are intentionally strict:

- implementation attempts: `1`
- implementation timeout: `1200` seconds
- structured review timeout: `600` seconds
- post-repair verification timeout: `300` seconds

The first Kimi prompt asks for a compact execution contract before editing:
likely files, acceptance checks, and non-goals. This counters the failure mode
seen in P1-02, where broad rediscovery consumed most of the attempt budget.
Kimi is also instructed to run targeted local checks only because the controller
runs the full quality gates immediately afterward.

Repair attempts use a different prompt. They treat the existing working tree as
the previous draft, start from the review failure and cited files, and avoid
restarting from the PRD or repo-wide discovery. When a repair follows structured
review findings, the post-repair Codex review verifies the original findings
instead of performing another full diff review.

Failures are split into two classes:

- Gate failures and structured review decisions are actionable, so the
  controller can run one focused Codex repair pass.
- Implementation timeouts with a newly produced candidate diff are verifier
  inputs, not automatic failures. The controller records the timeout and still
  runs its own gates and review.
- Implementation command failures, review infrastructure failures, timeouts
  without a new candidate diff, interrupted turns, or missing JSON are not
  actionable implementation feedback. The loop stops and leaves an inspectable
  blocked state instead of spending another implementation attempt.

## Commands

Use the existing Make targets:

```bash
make task-loop-one-kimi
make task-loop-one-commit-kimi
```

Override budgets only when a task is known to need it:

```bash
python3 scripts/run_task_loop.py run --kimi --max-attempts 3 --agent-timeout-seconds 1800
```
