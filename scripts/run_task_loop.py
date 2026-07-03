#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "tasks" / "ledger.json"
REVIEW_SCHEMA_PATH = ROOT / "tasks" / "review-decision.schema.json"
RUNS_DIR = ROOT / ".task-runs"
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_AGENT_TIMEOUT_SECONDS = 3600
DEFAULT_REVIEW_TIMEOUT_SECONDS = 1800
FAILURE_CONTEXT_MAX_CHARS = 16_000
FAILURE_LOG_TAIL_LINES = 120
AGENT_CODEX = "codex"
AGENT_KIMI = "kimi"


class LoopError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_ledger() -> dict[str, Any]:
    with LEDGER_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def save_ledger(ledger: dict[str, Any]) -> None:
    ledger["updated_at"] = utc_now()
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


def task_map(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {task["id"]: task for task in ledger["tasks"]}


def cluster_map(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {cluster["id"]: cluster for cluster in ledger["clusters"]}


def selected_cluster(ledger: dict[str, Any], cluster_id: str | None) -> dict[str, Any]:
    clusters = cluster_map(ledger)
    selected = cluster_id or ledger["active_cluster"]
    try:
        return clusters[selected]
    except KeyError as exc:
        choices = ", ".join(sorted(clusters))
        raise LoopError(f"Unknown cluster {selected!r}. Available clusters: {choices}") from exc


def cluster_queue(ledger: dict[str, Any], cluster_id: str | None) -> list[dict[str, Any]]:
    if cluster_id:
        return [selected_cluster(ledger, cluster_id)]

    active = selected_cluster(ledger, None)
    clusters = ledger["clusters"]
    active_index = next(
        index for index, cluster in enumerate(clusters) if cluster["id"] == active["id"]
    )
    return clusters[active_index:]


def pending_task_selection(
    ledger: dict[str, Any], cluster_id: str | None
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    tasks = task_map(ledger)
    for cluster in cluster_queue(ledger, cluster_id):
        pending = [
            tasks[task_id] for task_id in cluster["tasks"] if tasks[task_id]["status"] != "complete"
        ]
        if pending:
            return cluster, pending
    return None, []


def pending_tasks(ledger: dict[str, Any], cluster_id: str | None) -> list[dict[str, Any]]:
    _, tasks = pending_task_selection(ledger, cluster_id)
    return tasks


def run_logged(
    command: list[str],
    log_file: Path,
    input_text: str | None = None,
    timeout_seconds: int | None = None,
) -> int:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n\n")
        log.flush()
        try:
            process = subprocess.run(
                command,
                cwd=ROOT,
                input=input_text,
                text=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            log.write(f"\n[timeout_seconds] {timeout_seconds}\n")
            log.write("[exit_code] 124\n")
            return 124
        log.write(f"\n[exit_code] {process.returncode}\n")
    return process.returncode


def normalize_timeout_seconds(value: int) -> int | None:
    if value < 0:
        raise LoopError("Timeout values must be non-negative; use 0 to disable a timeout.")
    if value == 0:
        return None
    return value


def read_failure_context(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Could not read {path}: {exc}"

    if len(text) <= FAILURE_CONTEXT_MAX_CHARS:
        return text
    return text[-FAILURE_CONTEXT_MAX_CHARS:]


def read_log_tail(path: Path) -> str:
    text = read_failure_context(path)
    lines = text.splitlines()
    tail = "\n".join(lines[-FAILURE_LOG_TAIL_LINES:])
    if len(tail) <= FAILURE_CONTEXT_MAX_CHARS:
        return tail
    return tail[-FAILURE_CONTEXT_MAX_CHARS:]


def format_logged_failure(summary: str, log_file: Path) -> str:
    return f"{summary}; see {log_file}\n\nLog tail:\n{read_log_tail(log_file)}"


def format_review_failure(output_file: Path, decision: dict[str, Any]) -> str:
    return (
        f"review failed; see {output_file}\n\n"
        "Review decision JSON:\n"
        f"{json.dumps(decision, indent=2, sort_keys=True)}"
    )


def check_clean_worktree(allow_dirty: bool) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise LoopError(result.stderr.strip() or "Unable to inspect git status.")
    if result.stdout.strip() and not allow_dirty:
        raise LoopError(
            "Worktree is dirty. Commit, stash, or rerun with --allow-dirty if this is intentional."
        )


def implementation_prompt(task: dict[str, Any], attempt: int, previous_failure: str | None) -> str:
    prompt_path = ROOT / task["prompt"]
    task_prompt = prompt_path.read_text(encoding="utf-8")
    failure_block = ""
    if previous_failure:
        failure_block = (
            "\nPrevious loop attempt failed. Repair only the active task and the reported issues.\n"
            "Use the concrete failure details below; do not require a human to re-copy them.\n\n"
            f"{previous_failure}\n"
        )
    return f"""You are executing one bounded Baseline task slice.

Task: {task["id"]} - {task["title"]}
Attempt: {attempt}

Rules:
- Stay inside this task's scope.
- Follow AGENTS.md, the PRD, and the task prompt below.
- Do not begin later tasks.
- Keep changes surgical and privacy-safe.
- Add or update tests required by the task.
- The controller will run make fmt, make lint, make typecheck, make test,
  and a review gate after you finish.
{failure_block}
Task prompt:

{task_prompt}
"""


def review_prompt(task: dict[str, Any]) -> str:
    return f"""Review the current repository diff for Baseline task {task["id"]}: {task["title"]}.

Use a code-review stance. Return JSON matching the provided schema.

Decision rules:
- decision="pass" only if there are no blocker or major findings.
- decision="fail" for correctness bugs, privacy leaks, missing required tests,
  schema/API contract drift, or task-scope gaps.
- Keep findings grounded in files and line numbers when possible.
- Do not suggest unrelated refactors.
"""


def run_quality_gates(task_run_dir: Path, ledger: dict[str, Any]) -> tuple[bool, str]:
    failures: list[str] = []
    for index, gate in enumerate(ledger["quality_gates"], start=1):
        command = gate.split()
        log_file = task_run_dir / f"{index:02d}-gate-{'-'.join(command)}.log"
        print(f"  gate: {gate}")
        code = run_logged(command, log_file)
        if code != 0:
            failures.append(format_logged_failure(f"{gate} failed", log_file))
            break
    if failures:
        return False, "\n".join(failures)
    return True, "quality gates passed"


def run_review(
    task: dict[str, Any],
    task_run_dir: Path,
    codex_bin: str,
    review_timeout_seconds: int | None,
) -> tuple[bool, str]:
    output_file = task_run_dir / "review-decision.json"
    log_file = task_run_dir / "review.log"
    command = [
        codex_bin,
        "exec",
        "-C",
        str(ROOT),
        "--sandbox",
        "read-only",
        "--output-schema",
        str(REVIEW_SCHEMA_PATH),
        "--output-last-message",
        str(output_file),
        "-",
    ]
    print("  review: codex structured review")
    code = run_logged(
        command,
        log_file,
        review_prompt(task),
        timeout_seconds=review_timeout_seconds,
    )
    if code != 0:
        return False, format_logged_failure("review command failed", log_file)
    try:
        decision = json.loads(output_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, (
            f"review output was not valid JSON: {exc}; see {output_file}\n\n"
            f"Review log tail:\n{read_log_tail(log_file)}"
        )
    if decision["decision"] != "pass":
        return False, format_review_failure(output_file, decision)
    return True, f"review passed; see {output_file}"


def implementation_agent_command(args: argparse.Namespace) -> tuple[str, list[str]]:
    if args.agent == AGENT_CODEX:
        return (
            "codex exec",
            [
                args.codex_bin,
                "exec",
                "-C",
                str(ROOT),
                "--sandbox",
                "workspace-write",
                "-",
            ],
        )
    if args.agent == AGENT_KIMI:
        return "kimi --yolo", [args.kimi_bin, "--yolo"]
    raise LoopError(f"Unknown implementation agent: {args.agent}")


def commit_task(task: dict[str, Any]) -> None:
    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    if not status.stdout.strip():
        raise LoopError("No changes to commit after task completion.")
    scope = task["id"].split("-")[0].lower()
    message = f"feat({scope}): complete {task['id']} {task['title']}\n\n"
    message += "Constraint: Baseline loop automation requires one verified task slice per commit.\n"
    message += "Confidence: high\n"
    message += "Scope-risk: narrow\n"
    message += "Directive: Keep future task commits limited to the active ledger slice.\n"
    message += "Tested: make fmt; make lint; make typecheck; make test; codex structured review.\n"
    message += "Not-tested: None.\n"
    subprocess.run(["git", "commit", "-m", message], cwd=ROOT, check=True)


def complete_task(
    ledger: dict[str, Any],
    task: dict[str, Any],
    task_run_dir: Path,
    commit: bool,
) -> None:
    task["status"] = "complete"
    task["completed_at"] = utc_now()
    task["last_run_dir"] = str(task_run_dir.relative_to(ROOT))
    save_ledger(ledger)
    if commit:
        commit_task(task)


def run_task(
    ledger: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    print(f"task: {task['id']} - {task['title']}")
    previous_failure: str | None = None
    agent_timeout_seconds = normalize_timeout_seconds(args.agent_timeout_seconds)
    review_timeout_seconds = normalize_timeout_seconds(args.review_timeout_seconds)
    for attempt in range(1, args.max_attempts + 1):
        task_run_dir = RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-attempt-{attempt}"
        prompt = implementation_prompt(task, attempt, previous_failure)
        agent_label, command = implementation_agent_command(args)
        print(f"  attempt {attempt}: {agent_label}")
        exec_log = task_run_dir / f"{args.agent}-exec.log"
        code = run_logged(command, exec_log, prompt, timeout_seconds=agent_timeout_seconds)
        if code != 0:
            previous_failure = format_logged_failure(f"{agent_label} failed", exec_log)
            print(f"  failed: {previous_failure}")
            continue

        gates_ok, gate_result = run_quality_gates(task_run_dir, ledger)
        if not gates_ok:
            previous_failure = gate_result
            print(f"  failed: {gate_result}")
            continue

        if not args.skip_review:
            review_ok, review_result = run_review(
                task,
                task_run_dir,
                args.codex_bin,
                review_timeout_seconds,
            )
            if not review_ok:
                previous_failure = review_result
                print(f"  failed: {review_result}")
                continue
            print(f"  {review_result}")

        complete_task(ledger, task, task_run_dir, args.commit)
        print(f"  complete: {task['id']}")
        return True

    print(f"blocked: {task['id']} after {args.max_attempts} attempt(s)")
    if previous_failure:
        print(previous_failure)
    return False


def print_status(ledger: dict[str, Any], cluster_id: str | None) -> None:
    clusters = [selected_cluster(ledger, cluster_id)] if cluster_id else ledger["clusters"]
    tasks = task_map(ledger)
    for cluster in clusters:
        print(f"{cluster['id']}: {cluster['description']}")
        for task_id in cluster["tasks"]:
            task = tasks[task_id]
            print(f"  {task['status']:>8}  {task['id']}  {task['title']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Baseline task slices through a bounded loop.")
    parser.add_argument("--ledger", default=str(LEDGER_PATH), help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("status", "next"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--cluster")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--cluster")
    run_parser.add_argument("--task", help="Run one specific task id from the ledger.")
    run_parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Number of tasks to run; 0 means all pending tasks in the cluster.",
    )
    run_parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    run_parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=DEFAULT_AGENT_TIMEOUT_SECONDS,
        help="Maximum runtime for each implementation agent attempt; 0 disables.",
    )
    run_parser.add_argument(
        "--review-timeout-seconds",
        type=int,
        default=DEFAULT_REVIEW_TIMEOUT_SECONDS,
        help="Maximum runtime for the structured review gate; 0 disables.",
    )
    run_parser.add_argument("--commit", action="store_true", help="Commit each completed task.")
    run_parser.add_argument("--allow-dirty", action="store_true")
    run_parser.add_argument("--skip-review", action="store_true")
    agent_group = run_parser.add_mutually_exclusive_group()
    agent_group.add_argument(
        "--codex",
        dest="agent",
        action="store_const",
        const=AGENT_CODEX,
        default=AGENT_CODEX,
        help="Run implementation attempts with Codex.",
    )
    agent_group.add_argument(
        "--kimi",
        dest="agent",
        action="store_const",
        const=AGENT_KIMI,
        help="Run implementation attempts with Kimi Code via kimi --yolo.",
    )
    run_parser.add_argument("--codex-bin", default="codex")
    run_parser.add_argument("--kimi-bin", default="kimi")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if Path(args.ledger) != LEDGER_PATH:
        raise LoopError("Only the repo-local tasks/ledger.json is supported.")
    ledger = load_ledger()

    if args.command == "status":
        print_status(ledger, args.cluster)
        return 0

    if args.command == "next":
        tasks = pending_tasks(ledger, args.cluster)
        if not tasks:
            print("No pending tasks.")
            return 0
        task = tasks[0]
        print(f"{task['id']} {task['prompt']} - {task['title']}")
        return 0

    check_clean_worktree(args.allow_dirty)
    if args.task:
        candidates = [task_map(ledger)[args.task]]
    else:
        cluster, candidates = pending_task_selection(ledger, args.cluster)
        if cluster and not args.cluster:
            ledger["active_cluster"] = cluster["id"]
    if not candidates:
        print("No pending tasks.")
        return 0
    selected = candidates if args.limit == 0 else candidates[: args.limit]
    for task in selected:
        if not run_task(ledger, task, args):
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LoopError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
