#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "tasks" / "ledger.json"
REVIEW_SCHEMA_PATH = ROOT / "tasks" / "review-decision.schema.json"
RUNS_DIR = ROOT / ".task-runs"
CURRENT_RUN_PATH = RUNS_DIR / "current.json"
DEFAULT_MAX_ATTEMPTS = 1
DEFAULT_AGENT_TIMEOUT_SECONDS = 3600
DEFAULT_REVIEW_TIMEOUT_SECONDS = 600
DEFAULT_KIMI_MAX_ATTEMPTS = 1
DEFAULT_KIMI_AGENT_TIMEOUT_SECONDS = 1200
DEFAULT_KIMI_REVIEW_TIMEOUT_SECONDS = 600
DEFAULT_FINAL_REPAIR_TIMEOUT_SECONDS = 900
DEFAULT_REPAIR_REVIEW_TIMEOUT_SECONDS = 300
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_AGENT_LOG_LIMIT_BYTES = 2_000_000
DEFAULT_REVIEW_LOG_LIMIT_BYTES = 1_000_000
DONE_SENTINEL = "TASK_LOOP_DONE"
LOG_LIMIT_EXIT_CODE = 125
FAILURE_CONTEXT_MAX_CHARS = 6_000
FAILURE_LOG_TAIL_LINES = 80
CURRENT_LOG_TAIL_LINES = 40
AGENT_CODEX = "codex"
AGENT_KIMI = "kimi"
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\a]*(?:\a|\x1b\\))")
KIMI_INITIAL_GUIDANCE = """Kimi-specific execution contract:
- This is one non-interactive prompt. Keep the pass focused and stop once the task is ready
  for the controller gates.
- Start by reading the task prompt, current ledger state, and `git status --short`. Do not do
  a broad repo survey unless those files leave the implementation surface unclear.
- Before editing, write a compact contract in your log: files or symbols likely touched,
  acceptance checks, and explicit non-goals.
- Prefer a failing/targeted test first when behavior changes. If coverage already exists,
  name the covering test before editing.
- After edits, run the smallest relevant checks you can run locally. The controller will run
  the full quality gates; do not duplicate full-gate commands inside this pass unless they are
  needed to diagnose a failure.
- Before your final summary, inspect `git status --short`; untracked files are part of the
  diff even when `git diff --stat` is quiet.
"""
KIMI_REPAIR_GUIDANCE = """Kimi repair mode:
- Treat the existing working tree as the previous attempt's draft.
- Repair only the concrete gate/review failure details below; do not restart from
  broad PRD/repo discovery.
- Read the failure text and directly cited files first, then patch the smallest set of lines.
- Turn each review finding into an explicit checklist item and address every item before stopping.
- Add or adjust a regression test when the failure is behavioral.
- Before final summary, verify that generated artifacts such as __pycache__ are absent from
  `git status --short`.
- Stop after the focused repair and local targeted verification.
"""
CODEX_REPAIR_GUIDANCE = """Repair mode:
- Treat the existing working tree as the previous attempt's draft.
- Start from the concrete failure details below and any cited files or lines.
- Do not restart broad repo discovery unless the failure text is missing required context.
- Turn each review finding into an explicit checklist item and address every item before stopping.
- Add or adjust a regression test when the failure is behavioral.
- Run targeted checks for the touched behavior. The controller will run full quality gates after
  this pass.
- Before final summary, verify that generated artifacts such as __pycache__ are absent from
  `git status --short`.
- Stop after the focused repair and local targeted verification.
"""


class LoopError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_datetime() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def relative_to_root(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def format_elapsed(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {remainder:02d}s"


def git_status_snapshot() -> dict[str, Any]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return {"available": False, "summary": "git status unavailable"}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {
        "available": True,
        "changed_count": len(lines),
        "summary": "clean" if not lines else f"{len(lines)} changed file(s)",
        "files": lines[:20],
        "truncated": len(lines) > 20,
    }


def git_status_lines() -> list[str] | None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def implementation_has_candidate_changes(
    before: list[str] | None,
    after: list[str] | None,
) -> bool:
    return after is not None and bool(after) and after != before


def build_run_state(
    base_state: dict[str, Any],
    *,
    status: str,
    started_at: dt.datetime,
    command: list[str],
    command_label: str,
    log_file: Path,
    pid: int | None,
    timeout_seconds: int | None,
    exit_code: int | None = None,
) -> dict[str, Any]:
    now = utc_datetime()
    elapsed_seconds = int((now - started_at).total_seconds())
    state = {
        **base_state,
        "schema_version": 1,
        "status": status,
        "command": command,
        "command_label": command_label,
        "log_file": relative_to_root(log_file),
        "pid": pid,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "last_update_at": now.isoformat().replace("+00:00", "Z"),
        "elapsed_seconds": elapsed_seconds,
        "elapsed": format_elapsed(elapsed_seconds),
        "timeout_seconds": timeout_seconds,
        "exit_code": exit_code,
        "git_status": git_status_snapshot(),
    }
    if timeout_seconds is not None:
        remaining_seconds = max(0, timeout_seconds - elapsed_seconds)
        state["timeout_remaining_seconds"] = remaining_seconds
        state["timeout_remaining"] = format_elapsed(remaining_seconds)
    return state


def write_run_state(
    status_file: Path | None,
    base_state: dict[str, Any],
    *,
    status: str,
    started_at: dt.datetime,
    command: list[str],
    command_label: str,
    log_file: Path,
    pid: int | None,
    timeout_seconds: int | None,
    exit_code: int | None = None,
) -> dict[str, Any] | None:
    if status_file is None:
        return None
    state = build_run_state(
        base_state,
        status=status,
        started_at=started_at,
        command=command,
        command_label=command_label,
        log_file=log_file,
        pid=pid,
        timeout_seconds=timeout_seconds,
        exit_code=exit_code,
    )
    write_json_atomic(status_file, state)
    return state


def write_static_run_state(
    status_file: Path,
    base_state: dict[str, Any],
    *,
    status: str,
    stage: str,
    message: str | None = None,
) -> None:
    now = utc_now()
    previous_state: dict[str, Any] = {}
    if status_file.exists():
        try:
            previous_state = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous_state = {}
    state = {
        **previous_state,
        **base_state,
        "schema_version": 1,
        "status": status,
        "stage": stage,
        "message": message,
        "pid": None,
        "last_update_at": now,
        "git_status": git_status_snapshot(),
    }
    write_json_atomic(status_file, state)


def print_heartbeat(state: dict[str, Any]) -> None:
    print(
        "    running: "
        f"{state['command_label']} "
        f"elapsed {state['elapsed']} "
        f"pid {state['pid']} "
        f"log {state['log_file']} "
        f"changes {state['git_status']['summary']}"
    )
    sys.stdout.flush()


def terminate_process(process: subprocess.Popen[str]) -> int:
    process.terminate()
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()


def log_size_bytes(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def log_tail_has_exact_line(path: Path, needle: str, max_bytes: int = 32_768) -> bool:
    try:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(max(0, size - max_bytes))
            text = file.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    text = ANSI_ESCAPE_RE.sub("", text).replace("\r", "\n")
    return any(line.strip() == needle for line in text.splitlines())


def load_ledger() -> dict[str, Any]:
    with LEDGER_PATH.open(encoding="utf-8") as file:
        return cast(dict[str, Any], json.load(file))


def save_ledger(ledger: dict[str, Any]) -> None:
    write_json_atomic(LEDGER_PATH, {**ledger, "updated_at": utc_now()})


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
    clusters = cast(list[dict[str, Any]], ledger["clusters"])
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
    status_file: Path | None = None,
    status: dict[str, Any] | None = None,
    command_label: str | None = None,
    logged_command: list[str] | None = None,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    max_log_bytes: int | None = None,
    success_sentinel: str | None = None,
) -> int:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    base_state = status or {}
    label = command_label or " ".join(command)
    display_command = logged_command or command
    started_at = utc_datetime()
    with log_file.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(display_command) + "\n\n")
        log.flush()
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        if input_text is not None and process.stdin is not None:
            try:
                process.stdin.write(input_text)
                process.stdin.close()
            except BrokenPipeError:
                pass

        last_heartbeat = time.monotonic()
        write_run_state(
            status_file,
            base_state,
            status="running",
            started_at=started_at,
            command=display_command,
            command_label=label,
            log_file=log_file,
            pid=process.pid,
            timeout_seconds=timeout_seconds,
        )
        while True:
            returncode = process.poll()
            if returncode is not None:
                log.write(f"\n[exit_code] {returncode}\n")
                log.flush()
                write_run_state(
                    status_file,
                    base_state,
                    status="succeeded" if returncode == 0 else "failed",
                    started_at=started_at,
                    command=display_command,
                    command_label=label,
                    log_file=log_file,
                    pid=process.pid,
                    timeout_seconds=timeout_seconds,
                    exit_code=returncode,
                )
                return returncode

            elapsed_seconds = int((utc_datetime() - started_at).total_seconds())
            if success_sentinel and log_tail_has_exact_line(log_file, success_sentinel):
                terminate_process(process)
                log.write(f"\n[success_sentinel] {success_sentinel}\n")
                log.write("[exit_code] 0\n")
                log.flush()
                write_run_state(
                    status_file,
                    base_state,
                    status="succeeded",
                    started_at=started_at,
                    command=display_command,
                    command_label=label,
                    log_file=log_file,
                    pid=process.pid,
                    timeout_seconds=timeout_seconds,
                    exit_code=0,
                )
                return 0

            size_bytes = log_size_bytes(log_file)
            if max_log_bytes is not None and size_bytes is not None and size_bytes >= max_log_bytes:
                process.kill()
                process.wait()
                log.write(f"\n[max_log_bytes] {max_log_bytes}\n")
                log.write(f"[exit_code] {LOG_LIMIT_EXIT_CODE}\n")
                log.flush()
                write_run_state(
                    status_file,
                    base_state,
                    status="log_limited",
                    started_at=started_at,
                    command=display_command,
                    command_label=label,
                    log_file=log_file,
                    pid=process.pid,
                    timeout_seconds=timeout_seconds,
                    exit_code=LOG_LIMIT_EXIT_CODE,
                )
                return LOG_LIMIT_EXIT_CODE

            if timeout_seconds is not None and elapsed_seconds >= timeout_seconds:
                process.kill()
                process.wait()
                log.write(f"\n[timeout_seconds] {timeout_seconds}\n")
                log.write("[exit_code] 124\n")
                log.flush()
                write_run_state(
                    status_file,
                    base_state,
                    status="timed_out",
                    started_at=started_at,
                    command=display_command,
                    command_label=label,
                    log_file=log_file,
                    pid=process.pid,
                    timeout_seconds=timeout_seconds,
                    exit_code=124,
                )
                return 124

            if heartbeat_seconds > 0 and time.monotonic() - last_heartbeat >= heartbeat_seconds:
                state = write_run_state(
                    status_file,
                    base_state,
                    status="running",
                    started_at=started_at,
                    command=display_command,
                    command_label=label,
                    log_file=log_file,
                    pid=process.pid,
                    timeout_seconds=timeout_seconds,
                )
                if state is not None:
                    print_heartbeat(state)
                last_heartbeat = time.monotonic()

            time.sleep(1)


def normalize_timeout_seconds(value: int) -> int | None:
    if value < 0:
        raise LoopError("Timeout values must be non-negative; use 0 to disable a timeout.")
    if value == 0:
        return None
    return value


def normalize_log_limit_bytes(value: int) -> int | None:
    if value < 0:
        raise LoopError("Log limit values must be non-negative; use 0 to disable a log limit.")
    if value == 0:
        return None
    return value


def validate_heartbeat_seconds(value: int) -> None:
    if value < 0:
        raise LoopError("Heartbeat seconds must be non-negative; use 0 to disable heartbeats.")


def validate_positive_float(value: float, label: str) -> None:
    if value <= 0:
        raise LoopError(f"{label} must be greater than 0.")


def validate_positive_int(value: int, label: str) -> None:
    if value <= 0:
        raise LoopError(f"{label} must be greater than 0.")


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


def clean_log_tail(path: Path, line_count: int = CURRENT_LOG_TAIL_LINES) -> str:
    text = read_failure_context(path)
    text = ANSI_ESCAPE_RE.sub("", text).replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-line_count:])


def process_liveness(pid: int | None) -> str:
    if pid is None:
        return "unknown"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "not running"
    except PermissionError:
        return "unknown"
    return "running"


def latest_run_dir() -> Path | None:
    if not RUNS_DIR.exists():
        return None
    candidates = [path for path in RUNS_DIR.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def render_current_run(line_count: int = CURRENT_LOG_TAIL_LINES) -> str:
    if not CURRENT_RUN_PATH.exists():
        lines = ["No current task-loop state file."]
        latest = latest_run_dir()
        if latest is not None:
            lines.append(f"latest run dir: {relative_to_root(latest)}")
        return "\n".join(lines)

    try:
        state = json.loads(CURRENT_RUN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoopError(f"Could not read {CURRENT_RUN_PATH}: {exc}") from exc

    pid = state.get("pid")
    live = process_liveness(pid if isinstance(pid, int) else None)
    lines = [
        f"status: {state.get('status', 'unknown')} ({live})",
        f"task: {state.get('task_id', 'unknown')} - {state.get('task_title', 'unknown')}",
        f"stage: {state.get('stage', 'unknown')}",
        f"attempt: {state.get('attempt', '?')}/{state.get('max_attempts', '?')}",
        f"command: {state.get('command_label', 'unknown')}",
        f"elapsed: {current_elapsed(state)}",
    ]
    timeout_remaining = current_timeout_remaining(state)
    if timeout_remaining is not None:
        lines.append(f"timeout remaining: {timeout_remaining}")
    lines.extend(
        [
            f"run dir: {state.get('run_dir', 'unknown')}",
            f"log: {state.get('log_file', 'unknown')}",
        ]
    )
    git_status = state.get("git_status", {})
    if isinstance(git_status, dict):
        lines.append(f"git: {git_status.get('summary', 'unknown')}")

    log_name = state.get("log_file")
    if isinstance(log_name, str):
        log_path = ROOT / log_name
        if log_path.exists():
            tail = clean_log_tail(log_path, line_count)
            if tail:
                lines.extend(["", "log tail:", tail])
    return "\n".join(lines)


def current_elapsed(state: dict[str, Any]) -> str:
    started_at = parse_utc_timestamp(state.get("started_at"))
    if state.get("status") == "running" and started_at is not None:
        return format_elapsed(max(0, int((utc_datetime() - started_at).total_seconds())))
    elapsed = state.get("elapsed")
    return str(elapsed) if elapsed is not None else "unknown"


def current_timeout_remaining(state: dict[str, Any]) -> str | None:
    timeout_seconds = state.get("timeout_seconds")
    if not isinstance(timeout_seconds, int):
        return None
    started_at = parse_utc_timestamp(state.get("started_at"))
    if state.get("status") == "running" and started_at is not None:
        elapsed_seconds = max(0, int((utc_datetime() - started_at).total_seconds()))
        return format_elapsed(max(0, timeout_seconds - elapsed_seconds))
    timeout_remaining = state.get("timeout_remaining")
    return str(timeout_remaining) if timeout_remaining is not None else None


def parse_utc_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def print_current_run(line_count: int = CURRENT_LOG_TAIL_LINES) -> None:
    print(render_current_run(line_count))


def watch_current_run(interval_seconds: float, line_count: int) -> None:
    try:
        while True:
            if sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(render_current_run(line_count))
            print(f"\nrefreshing every {interval_seconds:g}s; press Ctrl-C to stop")
            sys.stdout.flush()
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\nstopped watching")


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


def implementation_guidance(agent: str, attempt: int, previous_failure: str | None) -> str:
    if agent != AGENT_KIMI:
        return f"\n{CODEX_REPAIR_GUIDANCE}" if attempt > 1 or previous_failure else ""
    if attempt > 1 or previous_failure:
        return f"\n{KIMI_REPAIR_GUIDANCE}"
    return f"\n{KIMI_INITIAL_GUIDANCE}"


def implementation_prompt(
    task: dict[str, Any],
    attempt: int,
    previous_failure: str | None,
    agent: str = AGENT_CODEX,
) -> str:
    prompt_path = ROOT / task["prompt"]
    task_prompt = prompt_path.read_text(encoding="utf-8")
    failure_block = ""
    if previous_failure:
        failure_block = (
            "\nPrevious loop attempt failed. Repair only the active task and the reported issues.\n"
            "Use the concrete failure details below; do not require a human to re-copy them.\n\n"
            f"{previous_failure}\n"
        )
    guidance_block = implementation_guidance(agent, attempt, previous_failure)
    return f"""You are executing one bounded Baseline task slice.

Task: {task["id"]} - {task["title"]}
Attempt: {attempt}

Rules:
- Stay inside this task's scope.
- Follow AGENTS.md, the PRD, and the task prompt below.
- Do not begin later tasks.
- Keep changes surgical and privacy-safe.
- Add or update tests required by the task.
- Inside this implementation pass, run targeted checks only. The controller runs the full
  quality gates immediately after the pass.
- The controller will run make fmt, make lint, make typecheck, make test,
  and a review gate after you finish.
- When the task is ready for controller gates, stop. End your final response with exactly
  `{DONE_SENTINEL}` on its own line so the controller can stop waiting immediately.
{guidance_block}
{failure_block}
Task prompt:

{task_prompt}
"""


def review_scope_snapshot() -> str:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return "git status unavailable"
    return result.stdout.strip() or "clean"


def review_prompt(task: dict[str, Any], status_snapshot: str | None = None) -> str:
    prompt_path = ROOT / task["prompt"]
    task_prompt = prompt_path.read_text(encoding="utf-8")
    changed_files = status_snapshot if status_snapshot is not None else review_scope_snapshot()
    return f"""Review the current repository diff for Baseline task {task["id"]}: {task["title"]}.

Use a code-review stance. Return JSON matching the provided schema.

Review scope:
- Use the task prompt below as the source of truth; do not read the full PRD or broad docs unless
  a changed file has an unclear contract that the task prompt does not cover.
- Start from this changed-file snapshot, then inspect only files needed to assess this task:

{changed_files}

- Do not run build or test commands in the review sandbox. The controller already ran quality
  gates before review; missing or sandbox-limited validation belongs in residual_risk, not as a
  reason to explore the repo.

Decision rules:
- decision="pass" only if there are no blocker or major findings.
- decision="fail" for correctness bugs, privacy leaks, missing required tests,
  schema/API contract drift, or task-scope gaps.
- Keep findings grounded in files and line numbers when possible.
- Do not suggest unrelated refactors.

Task prompt:

{task_prompt}
"""


def repair_review_prompt(
    task: dict[str, Any],
    previous_failure: str,
    status_snapshot: str | None = None,
) -> str:
    prompt_path = ROOT / task["prompt"]
    task_prompt = prompt_path.read_text(encoding="utf-8")
    changed_files = status_snapshot if status_snapshot is not None else review_scope_snapshot()
    return f"""Verify the focused repair for Baseline task {task["id"]}: {task["title"]}.

Use a code-review stance. Return JSON matching the provided schema.

This is a repair verification, not a fresh full review.

Verification scope:
- Treat the previous actionable failure below as the checklist. Inspect the cited files/lines
  and directly relevant changed files only.
- Start from this changed-file snapshot:

{changed_files}

- Do not run build or test commands in the review sandbox. The controller already reran quality
  gates after the repair.
- Do not search for new task-scope gaps or re-review the whole diff from scratch.

Decision rules:
- decision="pass" when the previous blocker/major findings or gate failure are resolved and
  the repair did not introduce an obvious direct blocker/major regression.
- decision="fail" only for an unresolved previous finding, a failed repair of the cited issue,
  or an obvious direct blocker/major regression introduced by the repair.
- Put possible new unrelated concerns in residual_risk instead of failing this repair
  verification.
- Keep findings grounded in files and line numbers when possible.

Previous actionable failure:

{previous_failure}

Task prompt:

{task_prompt}
"""


def run_quality_gates(
    task_run_dir: Path,
    ledger: dict[str, Any],
    base_status: dict[str, Any],
    heartbeat_seconds: int,
) -> tuple[bool, str]:
    failures: list[str] = []
    for index, gate in enumerate(ledger["quality_gates"], start=1):
        command = gate.split()
        log_file = task_run_dir / f"{index:02d}-gate-{'-'.join(command)}.log"
        print(f"  gate: {gate} -> log {relative_to_root(log_file)}")
        code = run_logged(
            command,
            log_file,
            status_file=CURRENT_RUN_PATH,
            status={
                **base_status,
                "stage": "quality_gate",
                "gate": gate,
                "gate_index": index,
            },
            command_label=gate,
            heartbeat_seconds=heartbeat_seconds,
        )
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
    codex_lean: bool,
    review_timeout_seconds: int | None,
    review_log_limit_bytes: int | None,
    base_status: dict[str, Any],
    heartbeat_seconds: int,
    prompt_text: str | None = None,
    command_label: str = "codex structured review",
) -> tuple[bool, str]:
    output_file = task_run_dir / "review-decision.json"
    log_file = task_run_dir / "review.log"
    command = [
        *codex_exec_command(codex_bin, "read-only", lean=codex_lean),
        "--output-schema",
        str(REVIEW_SCHEMA_PATH),
        "--output-last-message",
        str(output_file),
        "-",
    ]
    print(f"  review: {command_label} -> log {relative_to_root(log_file)}")
    code = run_logged(
        command,
        log_file,
        prompt_text if prompt_text is not None else review_prompt(task),
        timeout_seconds=review_timeout_seconds,
        status_file=CURRENT_RUN_PATH,
        status={**base_status, "stage": "review"},
        command_label=command_label,
        heartbeat_seconds=heartbeat_seconds,
        max_log_bytes=review_log_limit_bytes,
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


def review_failure_is_actionable(review_result: str) -> bool:
    return "Review decision JSON" in review_result


def gate_failure_is_actionable(failure_result: str) -> bool:
    return any(
        failure_result.startswith(f"{gate} failed")
        for gate in ("make fmt", "make lint", "make typecheck", "make test")
    )


def failure_is_actionable(failure_result: str) -> bool:
    return review_failure_is_actionable(failure_result) or gate_failure_is_actionable(
        failure_result
    )


def codex_exec_command(codex_bin: str, sandbox: str, *, lean: bool) -> list[str]:
    command = [codex_bin, "exec"]
    if lean:
        command.extend(["--ignore-user-config", "--ephemeral", "--color", "never"])
    command.extend(["-C", str(ROOT), "--sandbox", sandbox])
    return command


def run_final_repair(
    ledger: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
    previous_failure: str,
) -> bool:
    task_run_dir = RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-final-repair"
    prompt = implementation_prompt(
        task,
        args.max_attempts + 1,
        previous_failure,
        agent=AGENT_CODEX,
    )
    command = [*codex_exec_command(args.codex_bin, "workspace-write", lean=args.codex_lean), "-"]
    base_status = {
        "task_id": task["id"],
        "task_title": task["title"],
        "attempt": args.max_attempts + 1,
        "max_attempts": args.max_attempts,
        "run_dir": relative_to_root(task_run_dir),
        "final_repair": True,
    }
    print(
        "  final repair: codex exec for actionable review findings "
        f"-> run {relative_to_root(task_run_dir)}"
    )
    exec_log = task_run_dir / "codex-final-repair.log"
    code = run_logged(
        command,
        exec_log,
        prompt,
        timeout_seconds=normalize_timeout_seconds(args.final_repair_timeout_seconds),
        status_file=CURRENT_RUN_PATH,
        status={**base_status, "stage": "final_repair", "agent": AGENT_CODEX},
        command_label="codex final repair",
        heartbeat_seconds=args.heartbeat_seconds,
        max_log_bytes=normalize_log_limit_bytes(args.agent_log_limit_bytes),
        success_sentinel=DONE_SENTINEL,
    )
    if code != 0:
        print(f"  failed: {format_logged_failure('codex final repair failed', exec_log)}")
        return False

    gates_ok, gate_result = run_quality_gates(
        task_run_dir,
        ledger,
        base_status,
        args.heartbeat_seconds,
    )
    if not gates_ok:
        print(f"  failed: {gate_result}")
        return False

    if not args.skip_review:
        is_repair_verification = review_failure_is_actionable(previous_failure)
        review_timeout_seconds = normalize_timeout_seconds(
            args.repair_review_timeout_seconds
            if is_repair_verification
            else args.review_timeout_seconds
        )
        prompt_text = (
            repair_review_prompt(task, previous_failure) if is_repair_verification else None
        )
        command_label = (
            "codex repair verification" if is_repair_verification else "codex structured review"
        )
        review_ok, review_result = run_review(
            task,
            task_run_dir,
            args.codex_bin,
            args.codex_lean,
            review_timeout_seconds,
            normalize_log_limit_bytes(args.review_log_limit_bytes),
            base_status,
            args.heartbeat_seconds,
            prompt_text=prompt_text,
            command_label=command_label,
        )
        if not review_ok:
            print(f"  failed: {review_result}")
            return False
        print(f"  {review_result}")

    complete_task(ledger, task, task_run_dir, args.commit)
    write_static_run_state(
        CURRENT_RUN_PATH,
        base_status,
        status="complete",
        stage="complete",
        message=f"completed {task['id']} after final repair",
    )
    print(f"  complete: {task['id']}")
    return True


def block_task(task: dict[str, Any], base_status: dict[str, Any], message: str) -> None:
    print(f"blocked: {task['id']}")
    write_static_run_state(
        CURRENT_RUN_PATH,
        base_status,
        status="blocked",
        stage="blocked",
        message=message,
    )


def run_finish_task(
    ledger: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    print(f"task: {task['id']} - {task['title']}")
    current_status_lines = git_status_lines()
    if current_status_lines is None:
        raise LoopError("Unable to inspect git status before finishing task.")
    if not current_status_lines and not args.allow_no_changes:
        raise LoopError(
            "No existing diff to finish. Implement the task first, or rerun with "
            "--allow-no-changes for an intentional verification-only finish."
        )

    task_run_dir = RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-finish"
    base_status = {
        "task_id": task["id"],
        "task_title": task["title"],
        "attempt": 0,
        "max_attempts": 0,
        "run_dir": relative_to_root(task_run_dir),
        "finish_existing_diff": True,
    }
    write_static_run_state(
        CURRENT_RUN_PATH,
        base_status,
        status="running",
        stage="finish_existing_diff",
        message="verifying existing implementation diff",
    )

    gates_ok, gate_result = run_quality_gates(
        task_run_dir,
        ledger,
        base_status,
        args.heartbeat_seconds,
    )
    if not gates_ok:
        print(f"  failed: {gate_result}")
        if (
            args.final_repair
            and failure_is_actionable(gate_result)
            and run_final_repair(ledger, task, args, gate_result)
        ):
            return True
        block_task(task, base_status, "finish blocked by quality gate failure")
        print(gate_result)
        return False

    if not args.skip_review:
        review_ok, review_result = run_review(
            task,
            task_run_dir,
            args.codex_bin,
            args.codex_lean,
            normalize_timeout_seconds(args.review_timeout_seconds),
            normalize_log_limit_bytes(args.review_log_limit_bytes),
            base_status,
            args.heartbeat_seconds,
        )
        if not review_ok:
            print(f"  failed: {review_result}")
            if (
                args.final_repair
                and review_failure_is_actionable(review_result)
                and run_final_repair(ledger, task, args, review_result)
            ):
                return True
            block_task(task, base_status, "finish blocked by review failure")
            print(review_result)
            return False
        print(f"  {review_result}")

    complete_task(ledger, task, task_run_dir, args.commit)
    write_static_run_state(
        CURRENT_RUN_PATH,
        base_status,
        status="complete",
        stage="complete",
        message=f"completed {task['id']} from existing diff",
    )
    print(f"  complete: {task['id']}")
    return True


def implementation_agent_command(args: argparse.Namespace) -> tuple[str, list[str]]:
    if args.agent == AGENT_CODEX:
        lean = getattr(args, "codex_lean", True)
        label = "codex exec (lean)" if lean else "codex exec"
        command = [*codex_exec_command(args.codex_bin, "workspace-write", lean=lean), "-"]
        return label, command
    if args.agent == AGENT_KIMI:
        return "kimi --prompt", [args.kimi_bin]
    raise LoopError(f"Unknown implementation agent: {args.agent}")


def implementation_agent_invocation(
    args: argparse.Namespace,
    prompt: str,
) -> tuple[str, list[str], str | None, list[str]]:
    agent_label, command = implementation_agent_command(args)
    if args.agent == AGENT_CODEX:
        return agent_label, command, prompt, command
    if args.agent == AGENT_KIMI:
        prompt_command = [*command, "--prompt", prompt]
        logged_command = [*command, "--prompt", "<task-prompt>"]
        return agent_label, prompt_command, None, logged_command
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
    agent_log_limit_bytes = normalize_log_limit_bytes(args.agent_log_limit_bytes)
    review_log_limit_bytes = normalize_log_limit_bytes(args.review_log_limit_bytes)
    for attempt in range(1, args.max_attempts + 1):
        task_run_dir = RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-attempt-{attempt}"
        prompt = implementation_prompt(task, attempt, previous_failure, agent=args.agent)
        agent_label, command, input_text, logged_command = implementation_agent_invocation(
            args,
            prompt,
        )
        base_status = {
            "task_id": task["id"],
            "task_title": task["title"],
            "attempt": attempt,
            "max_attempts": args.max_attempts,
            "run_dir": relative_to_root(task_run_dir),
        }
        initial_status_lines = git_status_lines()
        print(f"  attempt {attempt}: {agent_label} -> run {relative_to_root(task_run_dir)}")
        exec_log = task_run_dir / f"{args.agent}-exec.log"
        print(f"    log: {relative_to_root(exec_log)}")
        code = run_logged(
            command,
            exec_log,
            input_text,
            timeout_seconds=agent_timeout_seconds,
            status_file=CURRENT_RUN_PATH,
            status={**base_status, "stage": "implementation", "agent": args.agent},
            command_label=agent_label,
            logged_command=logged_command,
            heartbeat_seconds=args.heartbeat_seconds,
            max_log_bytes=agent_log_limit_bytes,
            success_sentinel=DONE_SENTINEL,
        )
        current_status_lines = git_status_lines()
        if code != 0:
            if code in (124, LOG_LIMIT_EXIT_CODE) and implementation_has_candidate_changes(
                initial_status_lines,
                current_status_lines,
            ):
                stop_reason = "timed out" if code == 124 else "hit the log limit"
                previous_failure = format_logged_failure(
                    f"{agent_label} {stop_reason} after producing candidate changes",
                    exec_log,
                )
                print(
                    f"  {stop_reason}: implementation produced candidate changes; "
                    "continuing to controller gates"
                )
                write_static_run_state(
                    CURRENT_RUN_PATH,
                    base_status,
                    status="running",
                    stage=(
                        "implementation_timeout_candidate"
                        if code == 124
                        else "implementation_log_limit_candidate"
                    ),
                    message=(
                        f"implementation {stop_reason} after changing files; "
                        "continuing to controller gates"
                    ),
                )
            else:
                previous_failure = format_logged_failure(f"{agent_label} failed", exec_log)
                print(f"  failed: {previous_failure}")
                write_static_run_state(
                    CURRENT_RUN_PATH,
                    base_status,
                    status="blocked",
                    stage=(
                        "implementation_timeout"
                        if code == 124
                        else "implementation_log_limit"
                        if code == LOG_LIMIT_EXIT_CODE
                        else "implementation"
                    ),
                    message=(
                        "implementation attempt failed; "
                        "stopped before launching another broad retry"
                    ),
                )
                return False

        if code != 0 and previous_failure is not None:
            print("  note: budget-stop details retained for run history; gates decide task outcome")

        if (
            not args.allow_no_changes
            and initial_status_lines == []
            and not implementation_has_candidate_changes(
                initial_status_lines,
                current_status_lines,
            )
        ):
            previous_failure = (
                f"{agent_label} produced no candidate diff; skipped gates and review. "
                "If this is an intentional verification-only task, rerun with --allow-no-changes."
            )
            print(f"  blocked: {previous_failure}")
            write_static_run_state(
                CURRENT_RUN_PATH,
                base_status,
                status="blocked",
                stage="implementation_no_changes",
                message=previous_failure,
            )
            return False

        gates_ok, gate_result = run_quality_gates(
            task_run_dir,
            ledger,
            base_status,
            args.heartbeat_seconds,
        )
        if not gates_ok:
            previous_failure = gate_result
            print(f"  failed: {gate_result}")
            # Gate failures are not retried; fall through to final-repair logic below.
            break

        if not args.skip_review:
            review_ok, review_result = run_review(
                task,
                task_run_dir,
                args.codex_bin,
                args.codex_lean,
                review_timeout_seconds,
                review_log_limit_bytes,
                base_status,
                args.heartbeat_seconds,
            )
            if not review_ok:
                previous_failure = review_result
                print(f"  failed: {review_result}")
                if not review_failure_is_actionable(review_result):
                    write_static_run_state(
                        CURRENT_RUN_PATH,
                        base_status,
                        status="blocked",
                        stage="review",
                        message=(
                            "review gate did not produce actionable findings; "
                            "stopped before a repair attempt"
                        ),
                    )
                    return False
                break
            print(f"  {review_result}")

        complete_task(ledger, task, task_run_dir, args.commit)
        write_static_run_state(
            CURRENT_RUN_PATH,
            base_status,
            status="complete",
            stage="complete",
            message=f"completed {task['id']}",
        )
        print(f"  complete: {task['id']}")
        return True

    if (
        args.final_repair
        and previous_failure
        and failure_is_actionable(previous_failure)
        and run_final_repair(ledger, task, args, previous_failure)
    ):
        return True

    print(f"blocked: {task['id']} after {args.max_attempts} attempt(s)")
    write_static_run_state(
        CURRENT_RUN_PATH,
        {
            "task_id": task["id"],
            "task_title": task["title"],
            "attempt": args.max_attempts,
            "max_attempts": args.max_attempts,
        },
        status="blocked",
        stage="blocked",
        message=f"blocked after {args.max_attempts} attempt(s)",
    )
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
    current_parser = subparsers.add_parser("current")
    current_parser.add_argument("--watch", action="store_true", help="Refresh until interrupted.")
    current_parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between watch refreshes.",
    )
    current_parser.add_argument(
        "--tail-lines",
        type=int,
        default=CURRENT_LOG_TAIL_LINES,
        help="Number of cleaned log lines to show.",
    )

    finish_parser = subparsers.add_parser(
        "finish",
        help="Verify, review, and complete an existing implementation diff.",
    )
    finish_parser.add_argument("--cluster")
    finish_parser.add_argument("--task", help="Finish one specific task id from the ledger.")
    finish_parser.add_argument(
        "--review-timeout-seconds",
        type=int,
        default=DEFAULT_REVIEW_TIMEOUT_SECONDS,
        help="Maximum runtime for the structured review gate; 0 disables.",
    )
    finish_parser.add_argument(
        "--agent-log-limit-bytes",
        type=int,
        default=DEFAULT_AGENT_LOG_LIMIT_BYTES,
        help="Maximum final-repair log size before stopping; 0 disables.",
    )
    finish_parser.add_argument(
        "--review-log-limit-bytes",
        type=int,
        default=DEFAULT_REVIEW_LOG_LIMIT_BYTES,
        help="Maximum structured-review log size before stopping; 0 disables.",
    )
    finish_parser.add_argument(
        "--final-repair-timeout-seconds",
        type=int,
        default=DEFAULT_FINAL_REPAIR_TIMEOUT_SECONDS,
        help="Maximum runtime for the bounded Codex final-repair pass; 0 disables.",
    )
    finish_parser.add_argument(
        "--repair-review-timeout-seconds",
        type=int,
        default=DEFAULT_REPAIR_REVIEW_TIMEOUT_SECONDS,
        help="Maximum runtime for the focused post-repair review gate; 0 disables.",
    )
    finish_parser.add_argument(
        "--no-final-repair",
        dest="final_repair",
        action="store_false",
        help="Disable the bounded Codex repair pass after actionable findings.",
    )
    finish_parser.set_defaults(final_repair=True)
    finish_parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Seconds between live progress heartbeats; 0 disables heartbeat printing.",
    )
    finish_parser.add_argument("--commit", action="store_true", help="Commit the completed task.")
    finish_parser.add_argument(
        "--allow-no-changes",
        action="store_true",
        help="Allow gates/review even when there is no existing implementation diff.",
    )
    finish_parser.add_argument("--skip-review", action="store_true")
    finish_parser.add_argument("--codex-bin", default="codex")
    finish_parser.add_argument(
        "--codex-full-config",
        dest="codex_lean",
        action="store_false",
        help="Load full Codex user config instead of the lean automation invocation.",
    )
    finish_parser.set_defaults(codex_lean=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--cluster")
    run_parser.add_argument("--task", help="Run one specific task id from the ledger.")
    run_parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Number of tasks to run; 0 means all pending tasks in the cluster.",
    )
    run_parser.add_argument("--max-attempts", type=int)
    run_parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=None,
        help="Maximum runtime for each implementation agent attempt; 0 disables.",
    )
    run_parser.add_argument(
        "--review-timeout-seconds",
        type=int,
        default=None,
        help="Maximum runtime for the structured review gate; 0 disables.",
    )
    run_parser.add_argument(
        "--agent-log-limit-bytes",
        type=int,
        default=DEFAULT_AGENT_LOG_LIMIT_BYTES,
        help="Maximum implementation/final-repair log size before stopping; 0 disables.",
    )
    run_parser.add_argument(
        "--review-log-limit-bytes",
        type=int,
        default=DEFAULT_REVIEW_LOG_LIMIT_BYTES,
        help="Maximum structured-review log size before stopping; 0 disables.",
    )
    run_parser.add_argument(
        "--final-repair-timeout-seconds",
        type=int,
        default=DEFAULT_FINAL_REPAIR_TIMEOUT_SECONDS,
        help="Maximum runtime for the bounded Codex final-repair pass; 0 disables.",
    )
    run_parser.add_argument(
        "--repair-review-timeout-seconds",
        type=int,
        default=DEFAULT_REPAIR_REVIEW_TIMEOUT_SECONDS,
        help="Maximum runtime for the focused post-repair review gate; 0 disables.",
    )
    run_parser.add_argument(
        "--no-final-repair",
        dest="final_repair",
        action="store_false",
        help="Disable the bounded Codex repair pass after actionable Kimi review failures.",
    )
    run_parser.set_defaults(final_repair=True)
    run_parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Seconds between live progress heartbeats; 0 disables heartbeat printing.",
    )
    run_parser.add_argument("--commit", action="store_true", help="Commit each completed task.")
    run_parser.add_argument("--allow-dirty", action="store_true")
    run_parser.add_argument(
        "--allow-no-changes",
        action="store_true",
        help="Allow gates/review even when a clean implementation pass produces no diff.",
    )
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
        help="Run implementation attempts with Kimi Code via non-interactive prompt mode.",
    )
    run_parser.add_argument("--codex-bin", default="codex")
    run_parser.add_argument(
        "--codex-full-config",
        dest="codex_lean",
        action="store_false",
        help="Load full Codex user config instead of the lean automation invocation.",
    )
    run_parser.set_defaults(codex_lean=True)
    run_parser.add_argument("--kimi-bin", default="kimi")
    args = parser.parse_args()
    apply_agent_defaults(args)
    return args


def apply_agent_defaults(args: argparse.Namespace) -> None:
    if args.command != "run":
        return

    if args.agent == AGENT_KIMI:
        if args.max_attempts is None:
            args.max_attempts = DEFAULT_KIMI_MAX_ATTEMPTS
        if args.agent_timeout_seconds is None:
            args.agent_timeout_seconds = DEFAULT_KIMI_AGENT_TIMEOUT_SECONDS
        if args.review_timeout_seconds is None:
            args.review_timeout_seconds = DEFAULT_KIMI_REVIEW_TIMEOUT_SECONDS
        return

    if args.max_attempts is None:
        args.max_attempts = DEFAULT_MAX_ATTEMPTS
    if args.agent_timeout_seconds is None:
        args.agent_timeout_seconds = DEFAULT_AGENT_TIMEOUT_SECONDS
    if args.review_timeout_seconds is None:
        args.review_timeout_seconds = DEFAULT_REVIEW_TIMEOUT_SECONDS


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

    if args.command == "current":
        validate_positive_int(args.tail_lines, "tail lines")
        if args.watch:
            validate_positive_float(args.interval, "watch interval")
            watch_current_run(args.interval, args.tail_lines)
        else:
            print_current_run(args.tail_lines)
        return 0

    validate_heartbeat_seconds(args.heartbeat_seconds)
    # validate only — callers normalize inline before each use
    normalize_timeout_seconds(args.final_repair_timeout_seconds)
    normalize_timeout_seconds(args.repair_review_timeout_seconds)
    normalize_log_limit_bytes(args.agent_log_limit_bytes)
    normalize_log_limit_bytes(args.review_log_limit_bytes)
    if args.command == "finish":
        if args.task:
            candidates = [task_map(ledger)[args.task]]
        else:
            cluster, candidates = pending_task_selection(ledger, args.cluster)
            if cluster and not args.cluster:
                ledger["active_cluster"] = cluster["id"]
        if not candidates:
            print("No pending tasks.")
            return 0
        return 0 if run_finish_task(ledger, candidates[0], args) else 1

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
