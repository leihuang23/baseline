#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "tasks" / "ledger.json"
REVIEW_SCHEMA_PATH = ROOT / "tasks" / "review-decision.schema.json"
PROMPT_PACK_SCHEMA_PATH = ROOT / "tasks" / "prompt-pack.schema.json"
RUNS_DIR = ROOT / ".task-runs"
CURRENT_RUN_PATH = RUNS_DIR / "current.json"
DEFAULT_MAX_ATTEMPTS = 1
DEFAULT_AGENT_TIMEOUT_SECONDS = 3600
DEFAULT_REVIEW_TIMEOUT_SECONDS = 600
DEFAULT_FINAL_REPAIR_TIMEOUT_SECONDS = 900
DEFAULT_REPAIR_REVIEW_TIMEOUT_SECONDS = 300
DEFAULT_FINAL_REPAIR_ATTEMPTS = 0
DEFAULT_NO_PROGRESS_REPAIR_LIMIT = 3
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_AGENT_LOG_LIMIT_BYTES = 0
DEFAULT_REVIEW_LOG_LIMIT_BYTES = 0
DONE_SENTINEL = "TASK_LOOP_DONE"
LOG_LIMIT_EXIT_CODE = 125
FAILURE_CONTEXT_MAX_CHARS = 6_000
FAILURE_LOG_TAIL_LINES = 80
CURRENT_LOG_TAIL_LINES = 40
PASS_EVIDENCE_TERMS = ("passed", "pass", "success", "succeeded", "all checks passed")
AGENT_CODEX = "codex"
DECISION_LABEL_REVIEW = "Review"
DECISION_LABEL_AUDIT = "Audit"
PROTECTED_NON_AUTOMATION_PATHS = (
    "scripts/run_task_loop.py",
    "apps/api/tests/test_task_loop.py",
    "docs/automation/",
    "tasks/prompt-pack.schema.json",
    "tasks/review-decision.schema.json",
)
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\a]*(?:\a|\x1b\\))")
TOKEN_USAGE_RE = re.compile(r"tokens used\s*\n\s*([0-9][0-9,]*)", re.IGNORECASE)
FAILURE_SUMMARY_MAX_CHARS = 220
CODEX_REPAIR_GUIDANCE = """Repair mode:
- Treat the existing working tree as the current draft.
- Start from the concrete failure details below and any cited files or lines.
- Do not restart broad repo discovery unless the failure text is missing required context.
- Do not reread the full PRD, task corpus, or broad docs unless the cited failure lacks the
  contract needed to repair it.
- Turn each review finding into an explicit checklist item and address every item before stopping.
- Add or adjust a regression test when the failure is behavioral.
- Run the smallest targeted checks for the touched behavior. Prefer pytest with `--no-cov` for
  focused repair checks; the controller will run full quality gates with coverage after this pass.
- Keep stdout concise. Do not print full diffs, long file listings, or full test logs.
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


def git_status_paths() -> list[str]:
    lines = git_status_lines()
    if lines is None:
        raise LoopError("Unable to inspect git status.")
    paths: list[str] = []
    for line in lines:
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        paths.append(path)
    return paths


def git_status_entries() -> list[tuple[str, str]]:
    lines = git_status_lines()
    if lines is None:
        raise LoopError("Unable to inspect git status.")
    entries: list[tuple[str, str]] = []
    for line in lines:
        code = line[:2] if len(line) >= 2 else "??"
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        entries.append((code, path))
    return entries


def is_protected_non_automation_path(path: str) -> bool:
    return any(
        path == protected or path.startswith(protected)
        for protected in PROTECTED_NON_AUTOMATION_PATHS
    )


def product_status_paths_from_entries(
    task: dict[str, Any],
    entries: list[tuple[str, str]],
) -> set[str]:
    if task_allows_controller_changes(task):
        return {path for _code, path in entries}
    return {path for _code, path in entries if not is_protected_non_automation_path(path)}


def product_status_paths_from_lines(
    task: dict[str, Any],
    lines: list[str],
) -> set[str]:
    entries: list[tuple[str, str]] = []
    for line in lines:
        code = line[:2] if len(line) >= 2 else "??"
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        entries.append((code, path))
    return product_status_paths_from_entries(task, entries)


def iter_fingerprint_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(item for item in path.rglob("*") if item.is_file())


def git_worktree_fingerprint(*, exclude_protected: bool = False) -> str | None:
    try:
        entries = git_status_entries()
    except LoopError:
        return None
    digest = hashlib.sha256()
    for code, path in sorted(entries, key=lambda item: item[1]):
        if exclude_protected and is_protected_non_automation_path(path):
            continue
        digest.update(code.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        if code == "??":
            root_path = ROOT / path
            for file_path in iter_fingerprint_files(root_path):
                try:
                    relative_path = file_path.relative_to(ROOT)
                    content = file_path.read_bytes()
                except OSError:
                    continue
                digest.update(str(relative_path).encode("utf-8"))
                digest.update(b"\0")
                digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
                digest.update(b"\0")
            continue
        for diff_args in (
            ["git", "diff", "--binary", "--", path],
            ["git", "diff", "--cached", "--binary", "--", path],
        ):
            result = subprocess.run(diff_args, cwd=ROOT, capture_output=True, check=False)
            digest.update(result.stdout)
            digest.update(result.stderr)
            digest.update(str(result.returncode).encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def read_current_run_state() -> dict[str, Any] | None:
    if not CURRENT_RUN_PATH.exists():
        return None
    try:
        state = json.loads(CURRENT_RUN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoopError(f"Could not read {CURRENT_RUN_PATH}: {exc}") from exc
    if not isinstance(state, dict):
        raise LoopError(f"Current run state at {CURRENT_RUN_PATH} is not a JSON object.")
    return cast(dict[str, Any], state)


def scope_files_from_snapshot(status_snapshot: str) -> set[str]:
    files: set[str] = set()
    for raw_line in status_snapshot.splitlines():
        line = raw_line.rstrip()
        if not line or line == "clean" or line == "git status unavailable":
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path:
            files.add(path)
    return files


def public_prompt_pack(prompt_pack: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in prompt_pack.items() if not key.startswith("_")}


def prompt_pack_matches_scope(prompt_pack: dict[str, Any], status_snapshot: str) -> bool:
    previous = set(cast(list[str], prompt_pack.get("_scope_files", [])))
    current = scope_files_from_snapshot(status_snapshot)
    return bool(previous) and current.issubset(previous)


def extract_token_usage(log_file: Path) -> int | None:
    text = read_failure_context(log_file)
    matches = TOKEN_USAGE_RE.findall(text)
    if not matches:
        return None
    return int(matches[-1].replace(",", ""))


def record_stage_summary(log_file: Path, state: dict[str, Any]) -> None:
    entry = {
        "stage": state.get("stage"),
        "command_label": state.get("command_label"),
        "status": state.get("status"),
        "exit_code": state.get("exit_code"),
        "elapsed_seconds": state.get("elapsed_seconds"),
        "elapsed": state.get("elapsed"),
        "log_file": state.get("log_file"),
        "log_bytes": log_size_bytes(log_file),
        "tokens_used": extract_token_usage(log_file),
    }
    summary_file = log_file.parent / "run-summary.json"
    summary: dict[str, Any] = {"schema_version": 1, "stages": []}
    if summary_file.exists():
        try:
            loaded = json.loads(summary_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("stages"), list):
                summary = cast(dict[str, Any], loaded)
        except (OSError, json.JSONDecodeError):
            summary = {"schema_version": 1, "stages": []}
    stages = [item for item in summary["stages"] if item.get("log_file") != entry["log_file"]]
    stages.append(entry)
    summary["stages"] = stages
    write_json_atomic(summary_file, summary)


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
    if status != "running":
        state["git_fingerprint"] = git_worktree_fingerprint()
        state["git_non_protected_fingerprint"] = git_worktree_fingerprint(exclude_protected=True)
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
                state = write_run_state(
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
                if state is not None:
                    record_stage_summary(log_file, state)
                return returncode

            elapsed_seconds = int((utc_datetime() - started_at).total_seconds())
            if success_sentinel and log_tail_has_exact_line(log_file, success_sentinel):
                terminate_process(process)
                log.write(f"\n[success_sentinel] {success_sentinel}\n")
                log.write("[exit_code] 0\n")
                log.flush()
                state = write_run_state(
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
                if state is not None:
                    record_stage_summary(log_file, state)
                return 0

            size_bytes = log_size_bytes(log_file)
            if max_log_bytes is not None and size_bytes is not None and size_bytes >= max_log_bytes:
                process.kill()
                process.wait()
                log.write(f"\n[max_log_bytes] {max_log_bytes}\n")
                log.write(f"[exit_code] {LOG_LIMIT_EXIT_CODE}\n")
                log.flush()
                state = write_run_state(
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
                if state is not None:
                    record_stage_summary(log_file, state)
                return LOG_LIMIT_EXIT_CODE

            if timeout_seconds is not None and elapsed_seconds >= timeout_seconds:
                process.kill()
                process.wait()
                log.write(f"\n[timeout_seconds] {timeout_seconds}\n")
                log.write("[exit_code] 124\n")
                log.flush()
                state = write_run_state(
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
                if state is not None:
                    record_stage_summary(log_file, state)
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


def cleanup_generated_python_artifacts() -> int:
    removed = 0
    ignored_roots = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".task-runs",
        ".uv-cache",
        ".venv",
    }
    for pycache_dir in ROOT.rglob("__pycache__"):
        if any(part in ignored_roots for part in pycache_dir.relative_to(ROOT).parts):
            continue
        shutil.rmtree(pycache_dir, ignore_errors=True)
        removed += 1
    return removed


def resolve_root_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def gate_has_prior_pass_evidence(gate: str, evidence: str) -> bool:
    normalized = re.sub(r"\s+", " ", evidence.lower())
    normalized_gate = re.sub(r"\s+", " ", gate.lower())
    for match in re.finditer(re.escape(normalized_gate), normalized):
        window = normalized[match.start() : match.end() + 160]
        if any(term in window for term in PASS_EVIDENCE_TERMS):
            return True
    return False


def prior_verified_gates(
    ledger: dict[str, Any],
    prior_verification_file: str | None,
) -> tuple[set[str], Path | None]:
    if prior_verification_file is None:
        return set(), None

    evidence_path = resolve_root_relative_path(prior_verification_file)
    try:
        evidence = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoopError(f"Unable to read prior verification file {evidence_path}: {exc}") from exc

    verified = {
        gate for gate in ledger["quality_gates"] if gate_has_prior_pass_evidence(gate, evidence)
    }
    if not verified:
        raise LoopError(
            "Prior verification file did not prove any configured quality gate. "
            "Include lines such as 'make lint passed' or omit --prior-verification-file."
        )
    return verified, evidence_path


def write_prior_gate_log(log_file: Path, gate: str, evidence_path: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(
        "$ prior verification evidence\n\n"
        f"Reused prior successful evidence for: {gate}\n"
        f"Evidence file: {relative_to_root(evidence_path)}\n",
        encoding="utf-8",
    )


def write_prompt_snapshot(task_run_dir: Path, file_name: str, prompt_text: str) -> Path:
    prompt_file = task_run_dir / file_name
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt_text, encoding="utf-8")
    return prompt_file


def safe_artifact_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    return normalized or "audit"


def validate_prompt_pack(prompt_pack: dict[str, Any]) -> dict[str, Any]:
    for key in ("review_prompt", "audit_prompt"):
        if not isinstance(prompt_pack.get(key), str) or not prompt_pack[key].strip():
            raise LoopError(f"Prompt pack is missing non-empty {key!r}.")

    extra_audits = prompt_pack.get("extra_audits", [])
    if not isinstance(extra_audits, list):
        raise LoopError("Prompt pack extra_audits must be a list.")
    normalized_audits: list[dict[str, str]] = []
    for index, item in enumerate(extra_audits, start=1):
        if not isinstance(item, dict):
            raise LoopError("Prompt pack extra_audits entries must be objects.")
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise LoopError("Prompt pack extra audit is missing a non-empty prompt.")
        title = item.get("title")
        audit_id = safe_artifact_id(str(item.get("id") or title or f"extra-{index}"))
        normalized_audits.append(
            {
                "id": audit_id,
                "title": str(title or audit_id),
                "reason": str(item.get("reason") or ""),
                "prompt": prompt,
            }
        )

    pause_reasons = prompt_pack.get("pause_reasons", [])
    if not isinstance(pause_reasons, list):
        raise LoopError("Prompt pack pause_reasons must be a list.")

    return {
        **prompt_pack,
        "review_prompt": prompt_pack["review_prompt"].strip() + "\n",
        "audit_prompt": prompt_pack["audit_prompt"].strip() + "\n",
        "extra_audits": normalized_audits,
        "targeted_gates": [
            str(gate) for gate in prompt_pack.get("targeted_gates", []) if str(gate).strip()
        ],
        "pause_reasons": [str(reason) for reason in pause_reasons if str(reason).strip()],
        "requires_human_pause": bool(prompt_pack.get("requires_human_pause", False)),
        "summary": str(prompt_pack.get("summary") or ""),
    }


def fallback_prompt_pack(task: dict[str, Any], status_snapshot: str) -> dict[str, Any]:
    return validate_prompt_pack(
        {
            "summary": "Deterministic fallback prompt pack.",
            "review_prompt": review_prompt(task, status_snapshot),
            "audit_prompt": audit_prompt(task, status_snapshot),
            "extra_audits": [],
            "targeted_gates": [],
            "requires_human_pause": False,
            "pause_reasons": [],
        }
    )


def prompt_pack_generation_prompt(task: dict[str, Any], status_snapshot: str) -> str:
    source_prompt = implementation_prompt(task, 1, None)
    task_label = f"{task['id']}: {task['title']}"
    return f"""Generate the review/audit prompt pack for Baseline task {task_label}.

Return JSON matching the provided schema. This replaces the manual ChatGPT step where the
implementation prompt is converted into a review prompt and an audit prompt.

Generation rules:
- Produce a code-review prompt that is specific to this task prompt and changed-file snapshot.
- Produce an independent merge-readiness audit prompt that checks acceptance criteria,
  verification adequacy, integration drift, privacy/safety, and task-specific risks.
- Add extra_audits only when the task genuinely needs a separate check, such as UI state
  machines, API/schema/migration contracts, auth/permission boundaries, data deletion,
  evidence-backed reasoning, or eval/golden fixture behavior.
- Keep prompts bounded. Review/audit agents should inspect changed files and directly
  relevant symbols only; they should not run build/test commands because the controller owns
  executable gates.
- Set requires_human_pause=true only when a sensible human verification checkpoint is needed
  before automatically continuing to the next task.

Changed-file snapshot:

{status_snapshot}

Implementation prompt source:

{source_prompt}
"""


def write_prompt_pack_artifacts(task_run_dir: Path, prompt_pack: dict[str, Any]) -> None:
    write_json_atomic(task_run_dir / "prompt-pack.json", public_prompt_pack(prompt_pack))
    write_prompt_snapshot(task_run_dir, "generated-review-prompt.md", prompt_pack["review_prompt"])
    write_prompt_snapshot(task_run_dir, "generated-audit-prompt.md", prompt_pack["audit_prompt"])
    for audit in prompt_pack["extra_audits"]:
        write_prompt_snapshot(
            task_run_dir,
            f"extra-audit-{audit['id']}-prompt.md",
            audit["prompt"].strip() + "\n",
        )


def prepare_prompt_pack(
    task: dict[str, Any],
    task_run_dir: Path,
    args: argparse.Namespace,
    base_status: dict[str, Any],
    status_snapshot: str | None = None,
) -> dict[str, Any]:
    changed_files = status_snapshot if status_snapshot is not None else review_scope_snapshot()
    if getattr(args, "skip_prompt_pack", False) or (
        getattr(args, "skip_review", False) and getattr(args, "skip_audit", False)
    ):
        prompt_pack = fallback_prompt_pack(task, changed_files)
        prompt_pack["_scope_files"] = sorted(scope_files_from_snapshot(changed_files))
        write_prompt_pack_artifacts(task_run_dir, prompt_pack)
        return prompt_pack

    prompt = prompt_pack_generation_prompt(task, changed_files)
    prompt_file = write_prompt_snapshot(task_run_dir, "prompt-pack-generation-prompt.md", prompt)
    output_file = task_run_dir / "prompt-pack.json"
    log_file = task_run_dir / "prompt-pack-generation.log"
    command = [
        *codex_exec_command(args.codex_bin, "read-only", lean=args.codex_lean),
        "--output-schema",
        str(PROMPT_PACK_SCHEMA_PATH),
        "--output-last-message",
        str(output_file),
        "-",
    ]
    print(
        "  prompt-pack: codex generated review/audit prompts -> "
        f"log {relative_to_root(log_file)} prompt {relative_to_root(prompt_file)}"
    )
    code = run_logged(
        command,
        log_file,
        prompt,
        timeout_seconds=normalize_timeout_seconds(args.review_timeout_seconds),
        status_file=CURRENT_RUN_PATH,
        status={
            **base_status,
            "stage": "prompt_pack",
            "prompt_file": relative_to_root(prompt_file),
        },
        command_label="codex prompt pack",
        heartbeat_seconds=args.heartbeat_seconds,
        max_log_bytes=normalize_log_limit_bytes(args.review_log_limit_bytes),
    )
    if code != 0:
        raise LoopError(format_logged_failure("prompt-pack generation failed", log_file))

    try:
        prompt_pack = validate_prompt_pack(json.loads(output_file.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoopError(
            f"prompt-pack output was not valid JSON: {exc}; see {output_file}\n\n"
            f"Prompt-pack log tail:\n{read_log_tail(log_file)}"
        ) from exc
    prompt_pack["_scope_files"] = sorted(scope_files_from_snapshot(changed_files))
    write_prompt_pack_artifacts(task_run_dir, prompt_pack)
    return prompt_pack


def validate_heartbeat_seconds(value: int) -> None:
    if value < 0:
        raise LoopError("Heartbeat seconds must be non-negative; use 0 to disable heartbeats.")


def validate_positive_float(value: float, label: str) -> None:
    if value <= 0:
        raise LoopError(f"{label} must be greater than 0.")


def validate_positive_int(value: int, label: str) -> None:
    if value <= 0:
        raise LoopError(f"{label} must be greater than 0.")


def validate_non_negative_int(value: int, label: str) -> None:
    if value < 0:
        raise LoopError(f"{label} must be non-negative.")


def repair_limit_label(value: int) -> str:
    return "unlimited" if value == 0 else str(value)


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
        f"command: {state.get('command_label', 'unknown')}",
        f"elapsed: {current_elapsed(state)}",
    ]
    pass_label = state.get("pass_label")
    if isinstance(pass_label, str):
        lines.insert(3, f"pass: {pass_label}")
    elif state.get("final_repair"):
        repair_attempt = state.get("repair_attempt", "?")
        raw_repair_attempts = state.get("final_repair_attempts", "?")
        repair_attempts = (
            repair_limit_label(raw_repair_attempts)
            if isinstance(raw_repair_attempts, int)
            else raw_repair_attempts
        )
        repair_kind = state.get("repair_failure_kind", "unknown")
        lines.insert(3, f"repair: {repair_attempt}/{repair_attempts} ({repair_kind})")
    elif state.get("attempt") is not None:
        max_attempts = state.get("max_attempts", "?")
        if max_attempts and max_attempts != 1:
            lines.insert(3, f"implementation retry: {state.get('attempt', '?')}/{max_attempts}")
    timeout_remaining = current_timeout_remaining(state)
    if timeout_remaining is not None:
        lines.append(f"timeout remaining: {timeout_remaining}")
    lines.extend(
        [
            f"run dir: {state.get('run_dir', 'unknown')}",
            f"log: {state.get('log_file', 'unknown')}",
        ]
    )
    prompt_file = state.get("prompt_file")
    if isinstance(prompt_file, str):
        lines.append(f"prompt: {prompt_file}")
    stop_reason = state.get("stop_reason")
    if isinstance(stop_reason, str):
        lines.append(f"stop reason: {stop_reason}")
    current_failure_summary = state.get("current_failure_summary")
    if isinstance(current_failure_summary, str):
        lines.append(f"current finding: {current_failure_summary}")
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


def format_decision_failure(label: str, output_file: Path, decision: dict[str, Any]) -> str:
    return (
        f"{label.lower()} failed; see {output_file}\n\n"
        f"{label} decision JSON:\n"
        f"{json.dumps(decision, indent=2, sort_keys=True)}"
    )


def format_review_failure(output_file: Path, decision: dict[str, Any]) -> str:
    return format_decision_failure(DECISION_LABEL_REVIEW, output_file, decision)


def format_audit_failure(output_file: Path, decision: dict[str, Any]) -> str:
    return format_decision_failure(DECISION_LABEL_AUDIT, output_file, decision)


def decision_payload_from_failure(failure_result: str) -> dict[str, Any] | None:
    for marker in ("Review decision JSON:", "Audit decision JSON:"):
        marker_index = failure_result.find(marker)
        if marker_index == -1:
            continue
        payload = failure_result[marker_index + len(marker) :].strip()
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def normalized_failure_text(value: object, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def failure_progress_signature(failure_result: str) -> str:
    failure_kind = repair_failure_kind(failure_result) or "unknown"
    decision = decision_payload_from_failure(failure_result)
    if decision is not None:
        findings = decision.get("findings")
        if isinstance(findings, list) and findings:
            parts: list[str] = []
            for finding in findings:
                if not isinstance(finding, dict):
                    parts.append(normalized_failure_text(finding))
                    continue
                parts.append(
                    "|".join(
                        [
                            normalized_failure_text(finding.get("severity", "")),
                            normalized_failure_text(finding.get("file", "")),
                            normalized_failure_text(finding.get("line", "")),
                            normalized_failure_text(finding.get("message", "")),
                        ]
                    )
                )
            return f"{failure_kind}:findings:{'||'.join(parts)}"
        return f"{failure_kind}:summary:{normalized_failure_text(decision.get('summary', ''))}"
    first_line = failure_result.strip().splitlines()[0] if failure_result.strip() else ""
    return f"{failure_kind}:text:{normalized_failure_text(first_line)}"


def failure_status_summary(failure_result: str) -> str:
    decision = decision_payload_from_failure(failure_result)
    if decision is not None:
        findings = decision.get("findings")
        if isinstance(findings, list) and findings:
            first = findings[0]
            if isinstance(first, dict):
                location = str(first.get("file") or "unknown")
                line = first.get("line")
                if line is not None:
                    location = f"{location}:{line}"
                severity = str(first.get("severity") or "finding")
                message = normalized_failure_text(first.get("message", ""), 140)
                return normalized_failure_text(f"{severity} {location}: {message}")
        summary = decision.get("summary")
        if summary:
            return normalized_failure_text(summary, FAILURE_SUMMARY_MAX_CHARS)
    first_line = failure_result.strip().splitlines()[0] if failure_result.strip() else ""
    return normalized_failure_text(first_line, FAILURE_SUMMARY_MAX_CHARS)


def resumable_dirty_task(
    ledger: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    state = read_current_run_state()
    if state is None:
        return None

    pid = state.get("pid")
    if process_liveness(pid if isinstance(pid, int) else None) == "running":
        raise LoopError(
            "Current task-loop state still points at a running process. "
            "Use `python3 scripts/run_task_loop.py current --watch` "
            "instead of starting another run."
        )

    task_id = state.get("task_id")
    if not isinstance(task_id, str):
        return None
    if state.get("status") == "complete":
        return None
    if args.task and args.task != task_id:
        return None

    tasks = task_map(ledger)
    task = tasks.get(task_id)
    if task is None or task.get("status") == "complete":
        return None

    if args.cluster:
        cluster = selected_cluster(ledger, args.cluster)
        if task_id not in cluster["tasks"]:
            return None
    else:
        _, pending = pending_task_selection(ledger, None)
        if task_id not in {item["id"] for item in pending}:
            return None

    return task


def task_prompt_text(task: dict[str, Any]) -> str:
    prompt_path = ROOT / str(task["prompt"])
    return prompt_path.read_text(encoding="utf-8")


def implementation_guidance(attempt: int, previous_failure: str | None) -> str:
    return f"\n{CODEX_REPAIR_GUIDANCE}" if attempt > 1 or previous_failure else ""


def implementation_pass_label(
    attempt: int,
    max_attempts: int,
    previous_failure: str | None,
) -> str:
    if previous_failure:
        return f"implementation retry {attempt}"
    if max_attempts > 1:
        return f"implementation {attempt}/{max_attempts}"
    return "implementation"


def implementation_run_suffix(attempt: int, max_attempts: int) -> str:
    if max_attempts > 1:
        return f"implementation-{attempt}"
    return "implementation"


def implementation_prompt(
    task: dict[str, Any],
    attempt: int,
    previous_failure: str | None,
    pass_label: str | None = None,
) -> str:
    task_prompt = task_prompt_text(task)
    failure_block = ""
    if previous_failure:
        failure_block = (
            "\nPrevious loop attempt failed. Repair only the active task and the reported issues.\n"
            "Use the concrete failure details below; do not require a human to re-copy them.\n\n"
            f"{previous_failure}\n"
        )
    guidance_block = implementation_guidance(attempt, previous_failure)
    resolved_pass_label = pass_label or (
        f"implementation retry {attempt}" if previous_failure else "implementation"
    )
    return f"""You are executing one bounded Baseline task slice.

Task: {task["id"]} - {task["title"]}
Pass: {resolved_pass_label}

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
- Keep stdout concise. Do not print full diffs, long file listings, or full test logs.
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
    task_prompt = task_prompt_text(task)
    changed_files = status_snapshot if status_snapshot is not None else review_scope_snapshot()
    return f"""Review the current repository diff for Baseline task {task["id"]}: {task["title"]}.

Use a code-review stance. Return JSON matching the provided schema.

Review scope:
- Use the task prompt below as the source of truth; do not read the full PRD or broad docs unless
  a changed file has an unclear contract that the task prompt does not cover.
- Start from this changed-file snapshot, then inspect only files needed to assess this task:

{changed_files}

- Do not enumerate broad directories or test trees. Avoid commands such as `find apps/api/tests`
  or full-tree listings. For untracked directories, inspect the package entry points, changed
  files, and directly relevant tests by targeted path or symbol search only.
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
    task_prompt = task_prompt_text(task)
    changed_files = status_snapshot if status_snapshot is not None else review_scope_snapshot()
    return f"""Verify the focused repair for Baseline task {task["id"]}: {task["title"]}.

Use a code-review stance. Return JSON matching the provided schema.

This is a repair verification, not a fresh full review.

Verification scope:
- Treat the previous actionable failure below as the checklist. Inspect the cited files/lines
  and directly relevant changed files only.
- Start from this changed-file snapshot:

{changed_files}

- Do not enumerate broad directories or test trees. Avoid commands such as `find apps/api/tests`
  or full-tree listings. For untracked directories, inspect the cited files, package entry points,
  and directly relevant tests by targeted path or symbol search only.
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


def audit_focuses_for_task(task: dict[str, Any], status_snapshot: str) -> list[str]:
    task_prompt = task_prompt_text(task)
    haystack = "\n".join(
        [
            task["id"],
            task["title"],
            task["prompt"],
            status_snapshot,
            task_prompt,
        ]
    ).lower()
    focuses = [
        (
            "Task acceptance: every deliverable and acceptance criterion in the task prompt "
            "is covered."
        ),
        "Verification adequacy: tests or targeted checks prove the behavior that changed.",
        (
            "Privacy and safety: no real secrets, raw health data, free-text notes, prompt "
            "payloads, "
            "or medical-advice drift entered source, tests, logs, fixtures, or docs."
        ),
    ]
    if any(token in haystack for token in ("apps/ios/", ".swift", "swiftui", "ui", "view")):
        focuses.append(
            "Extra UI state-machine audit: loading, empty, success, failure, disabled, permission, "
            "offline, and retry states are reachable, non-overlapping, and backed by tests "
            "or previews where the task requires them."
        )
    if any(token in haystack for token in ("api", "schema", "contract", "alembic", "migration")):
        focuses.append(
            "Contract audit: API schemas, database migrations, repository contracts, and "
            "docs stay in sync."
        )
    if any(token in haystack for token in ("llm", "assistant", "briefing", "reasoning", "safety")):
        focuses.append(
            "Reasoning/safety audit: deterministic logic owns metrics, generated text "
            "carries evidence, and safety validation cannot be bypassed."
        )
    if any(token in haystack for token in ("eval", "golden", "scenario", "fixture")):
        focuses.append(
            "Evaluation audit: golden fixtures, scenario registration, and scoring "
            "expectations match the new behavior."
        )
    return focuses


def format_audit_focuses(focuses: list[str]) -> str:
    return "\n".join(f"- {focus}" for focus in focuses)


def audit_prompt(task: dict[str, Any], status_snapshot: str | None = None) -> str:
    task_prompt = task_prompt_text(task)
    changed_files = status_snapshot if status_snapshot is not None else review_scope_snapshot()
    focuses = audit_focuses_for_task(task, changed_files)
    return f"""Audit the current repository diff for Baseline task {task["id"]}: {task["title"]}.

Use an independent merge-readiness stance. Return JSON matching the provided schema.

Audit scope:
- This is not a second broad code review. Assume quality gates and the generated review already
  ran; spend the budget on acceptance gaps, missing verification, integration drift, and
  task-specific risks that a normal line review can miss.
- Start from this changed-file snapshot, then inspect only files needed for the audit focus:

{changed_files}

- Do not enumerate broad directories or test trees. Avoid commands such as `find apps/api/tests`
  or full-tree listings. Use targeted path or symbol search only.
- Do not run build or test commands in the audit sandbox. The controller owns executable gates.

Adaptive audit focus:
{format_audit_focuses(focuses)}

Decision rules:
- decision="pass" only if there are no blocker or major audit findings.
- decision="fail" for task-scope gaps, missing required tests, privacy/safety issues,
  schema/API/DB contract drift, invalid state-machine coverage, or unresolved verification gaps.
- Minor cleanup notes can be findings with decision="pass"; do not fail for unrelated refactors.
- Keep findings grounded in files and line numbers when possible.

Task prompt:

{task_prompt}
"""


def repair_audit_prompt(
    task: dict[str, Any],
    previous_failure: str,
    status_snapshot: str | None = None,
) -> str:
    task_prompt = task_prompt_text(task)
    changed_files = status_snapshot if status_snapshot is not None else review_scope_snapshot()
    focuses = audit_focuses_for_task(task, changed_files)
    return f"""Verify the focused repair audit for Baseline task {task["id"]}: {task["title"]}.

Use an independent merge-readiness stance. Return JSON matching the provided schema.

This is a repair audit, not a fresh full review.

Verification scope:
- Treat the previous actionable audit failure below as the checklist. Inspect the cited files/lines
  and directly relevant changed files only.
- Start from this changed-file snapshot:

{changed_files}

- Do not enumerate broad directories or test trees. Avoid commands such as `find apps/api/tests`
  or full-tree listings.
- Do not run build or test commands in the audit sandbox. The controller already reran quality
  gates.

Adaptive audit focus:
{format_audit_focuses(focuses)}

Decision rules:
- decision="pass" when the previous blocker/major audit findings are resolved and the repair did
  not introduce an obvious direct blocker/major regression.
- decision="fail" only for an unresolved previous audit finding, a failed repair of the cited issue,
  or an obvious direct blocker/major regression introduced by the repair.
- Put possible new unrelated concerns in residual_risk instead of failing this repair audit.

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
    verified_gates: set[str] | None = None,
    prior_verification_path: Path | None = None,
) -> tuple[bool, str]:
    failures: list[str] = []
    verified_gates = verified_gates or set()
    for index, gate in enumerate(ledger["quality_gates"], start=1):
        command = gate.split()
        log_file = task_run_dir / f"{index:02d}-gate-{'-'.join(command)}.log"
        if gate in verified_gates and prior_verification_path is not None:
            print(
                f"  gate: {gate} -> reused prior verification "
                f"{relative_to_root(prior_verification_path)}"
            )
            write_prior_gate_log(log_file, gate, prior_verification_path)
            continue
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
    stage: str = "review",
    output_name: str = "review-decision.json",
    log_name: str = "review.log",
    prompt_name: str = "review-prompt.md",
    decision_label: str = DECISION_LABEL_REVIEW,
) -> tuple[bool, str]:
    output_file = task_run_dir / output_name
    log_file = task_run_dir / log_name
    resolved_prompt = prompt_text if prompt_text is not None else review_prompt(task)
    prompt_file = write_prompt_snapshot(task_run_dir, prompt_name, resolved_prompt)
    command = [
        *codex_exec_command(codex_bin, "read-only", lean=codex_lean),
        "--output-schema",
        str(REVIEW_SCHEMA_PATH),
        "--output-last-message",
        str(output_file),
        "-",
    ]
    print(
        f"  {stage}: {command_label} -> log {relative_to_root(log_file)} "
        f"prompt {relative_to_root(prompt_file)}"
    )
    code = run_logged(
        command,
        log_file,
        resolved_prompt,
        timeout_seconds=review_timeout_seconds,
        status_file=CURRENT_RUN_PATH,
        status={**base_status, "stage": stage, "prompt_file": relative_to_root(prompt_file)},
        command_label=command_label,
        heartbeat_seconds=heartbeat_seconds,
        max_log_bytes=review_log_limit_bytes,
    )
    if code != 0:
        return False, format_logged_failure(f"{stage} command failed", log_file)
    try:
        decision = json.loads(output_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, (
            f"{stage} output was not valid JSON: {exc}; see {output_file}\n\n"
            f"{decision_label} log tail:\n{read_log_tail(log_file)}"
        )
    if decision["decision"] != "pass":
        return False, format_decision_failure(decision_label, output_file, decision)
    return True, f"{stage} passed; see {output_file}"


def run_audit(
    task: dict[str, Any],
    task_run_dir: Path,
    codex_bin: str,
    codex_lean: bool,
    audit_timeout_seconds: int | None,
    audit_log_limit_bytes: int | None,
    base_status: dict[str, Any],
    heartbeat_seconds: int,
    prompt_text: str | None = None,
    command_label: str = "codex adaptive audit",
    prompt_name: str = "audit-prompt.md",
    output_name: str = "audit-decision.json",
    log_name: str = "audit.log",
) -> tuple[bool, str]:
    return run_review(
        task,
        task_run_dir,
        codex_bin,
        codex_lean,
        audit_timeout_seconds,
        audit_log_limit_bytes,
        base_status,
        heartbeat_seconds,
        prompt_text=prompt_text if prompt_text is not None else audit_prompt(task),
        command_label=command_label,
        stage="audit",
        output_name=output_name,
        log_name=log_name,
        prompt_name=prompt_name,
        decision_label=DECISION_LABEL_AUDIT,
    )


def run_extra_audits(
    task: dict[str, Any],
    task_run_dir: Path,
    args: argparse.Namespace,
    prompt_pack: dict[str, Any],
    base_status: dict[str, Any],
) -> tuple[bool, str]:
    for audit in prompt_pack["extra_audits"]:
        audit_id = audit["id"]
        audit_ok, audit_result = run_audit(
            task,
            task_run_dir,
            args.codex_bin,
            args.codex_lean,
            normalize_timeout_seconds(args.review_timeout_seconds),
            normalize_log_limit_bytes(args.review_log_limit_bytes),
            base_status,
            args.heartbeat_seconds,
            prompt_text=audit["prompt"].strip() + "\n",
            command_label=f"codex extra audit: {audit['title']}",
            prompt_name=f"extra-audit-{audit_id}-prompt.md",
            output_name=f"extra-audit-{audit_id}-decision.json",
            log_name=f"extra-audit-{audit_id}.log",
        )
        if not audit_ok:
            return False, audit_result
        print(f"  {audit_result}")
    return True, "extra audits passed"


def pause_reasons_for_task(
    task: dict[str, Any],
    prompt_pack: dict[str, Any],
    status_snapshot: str,
) -> list[str]:
    reasons: list[str] = []
    if prompt_pack["requires_human_pause"]:
        reasons.extend(prompt_pack["pause_reasons"] or ["generated prompt pack requested a pause"])

    haystack = "\n".join(
        [
            str(task.get("id", "")),
            str(task.get("title", "")),
            str(task.get("prompt", "")),
            status_snapshot,
        ]
    ).lower()
    if any(token in haystack for token in ("apps/ios/", ".swift", "swiftui", " ui", "view")):
        reasons.append("UI/state-machine changes need human visual verification before continuing.")
    if any(
        token in haystack
        for token in (
            "alembic",
            "migration",
            "schema",
            "contract",
            "auth",
            "permission",
            "consent",
            "delete",
            "retention",
        )
    ):
        reasons.append("Contract, permission, or data-lifecycle changes need human verification.")
    if any(token in haystack for token in ("medical", "safety", "advice", "risk")):
        reasons.append("Safety-sensitive behavior needs human verification.")

    deduped: list[str] = []
    for reason in reasons:
        if reason and reason not in deduped:
            deduped.append(reason)
    return deduped


def remember_pause_reasons(
    args: argparse.Namespace,
    task: dict[str, Any],
    prompt_pack: dict[str, Any],
) -> list[str]:
    policy = getattr(args, "pause_policy", "auto")
    if policy == "never":
        reasons: list[str] = []
    elif policy == "always":
        reasons = ["pause policy is set to always."]
    else:
        reasons = pause_reasons_for_task(task, prompt_pack, review_scope_snapshot())
    args.last_pause_reasons = reasons
    if reasons:
        print("  pause: human verification recommended before the next task")
        for reason in reasons:
            print(f"    - {reason}")
    return reasons


def review_failure_is_actionable(review_result: str) -> bool:
    return "Review decision JSON" in review_result


def audit_failure_is_actionable(audit_result: str) -> bool:
    return "Audit decision JSON" in audit_result


def decision_failure_is_actionable(result: str) -> bool:
    return review_failure_is_actionable(result) or audit_failure_is_actionable(result)


def gate_failure_is_actionable(failure_result: str) -> bool:
    return any(
        failure_result.startswith(f"{gate} failed")
        for gate in ("make fmt", "make lint", "make typecheck", "make test")
    )


def failure_is_actionable(failure_result: str) -> bool:
    return decision_failure_is_actionable(failure_result) or gate_failure_is_actionable(
        failure_result
    )


def remember_final_repair_stop(
    args: argparse.Namespace,
    *,
    stop_reason: str,
    message: str,
    failure_result: str | None = None,
) -> None:
    args.last_stop_reason = stop_reason
    args.last_block_message = message
    if failure_result is not None:
        args.last_failure_summary = failure_status_summary(failure_result)


def codex_exec_command(codex_bin: str, sandbox: str, *, lean: bool) -> list[str]:
    command = [codex_bin, "exec"]
    if lean:
        command.extend(["--ignore-user-config", "--ephemeral", "--color", "never"])
    command.extend(["-C", str(ROOT), "--sandbox", sandbox])
    return command


def repair_failure_kind(failure_result: str) -> str | None:
    if gate_failure_is_actionable(failure_result):
        return "gate"
    if decision_failure_is_actionable(failure_result):
        return "decision"
    return None


def ensure_prompt_pack_for_scope(
    task: dict[str, Any],
    task_run_dir: Path,
    args: argparse.Namespace,
    base_status: dict[str, Any],
    prompt_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    current_snapshot = review_scope_snapshot()
    if prompt_pack is None:
        return prepare_prompt_pack(task, task_run_dir, args, base_status, current_snapshot)
    if prompt_pack_matches_scope(prompt_pack, current_snapshot):
        print("  prompt-pack: reusing existing generated review/audit prompts")
        write_prompt_pack_artifacts(task_run_dir, prompt_pack)
        return prompt_pack
    print("  prompt-pack: changed-file scope expanded; regenerating prompts")
    return prepare_prompt_pack(task, task_run_dir, args, base_status, current_snapshot)


def run_final_repair(
    ledger: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
    previous_failure: str,
    prompt_pack: dict[str, Any] | None = None,
) -> bool:
    max_attempts = getattr(args, "max_attempts", 0)
    repair_attempts = getattr(args, "final_repair_attempts", DEFAULT_FINAL_REPAIR_ATTEMPTS)
    no_progress_limit = getattr(
        args,
        "max_no_progress_repairs",
        DEFAULT_NO_PROGRESS_REPAIR_LIMIT,
    )
    current_failure = previous_failure
    attempts_by_kind = {"gate": 0, "decision": 0}
    repeated_no_progress: dict[tuple[str, str, str], int] = {}
    repair_index = 0
    while True:
        failure_kind = repair_failure_kind(current_failure)
        if failure_kind is None:
            print("  failed: final repair failure is not actionable")
            remember_final_repair_stop(
                args,
                stop_reason="non_actionable",
                message="final repair failure is not actionable",
                failure_result=current_failure,
            )
            return False
        if repair_attempts and attempts_by_kind[failure_kind] >= repair_attempts:
            message = f"exhausted {repair_attempts} {failure_kind} repair attempt(s)"
            print(f"  failed: {message}")
            remember_final_repair_stop(
                args,
                stop_reason="budget_exhausted",
                message=message,
                failure_result=current_failure,
            )
            return False
        failure_signature = failure_progress_signature(current_failure)
        failure_fingerprint = git_worktree_fingerprint(exclude_protected=True) or "unavailable"
        no_progress_key = (failure_kind, failure_signature, failure_fingerprint)
        no_progress_count = repeated_no_progress.get(no_progress_key, 0)
        if no_progress_limit and no_progress_count >= no_progress_limit:
            message = (
                "same actionable finding repeated without worktree progress "
                f"{no_progress_count} time(s)"
            )
            print(f"  failed: {message}")
            remember_final_repair_stop(
                args,
                stop_reason="no_progress",
                message=message,
                failure_result=current_failure,
            )
            return False
        repeated_no_progress[no_progress_key] = no_progress_count + 1
        attempts_by_kind[failure_kind] += 1
        repair_index += 1
        task_run_dir = (
            RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-final-repair-{repair_index}"
        )
        repair_pass_label = f"focused repair {repair_index}"
        prompt = implementation_prompt(
            task,
            max_attempts + repair_index,
            current_failure,
            repair_pass_label,
        )
        command = [
            *codex_exec_command(args.codex_bin, "workspace-write", lean=args.codex_lean),
            "-",
        ]
        base_status = {
            "task_id": task["id"],
            "task_title": task["title"],
            "attempt": max_attempts + repair_index,
            "max_attempts": max_attempts,
            "pass_label": repair_pass_label,
            "run_dir": relative_to_root(task_run_dir),
            "final_repair": True,
            "repair_attempt": repair_index,
            "final_repair_attempts": repair_attempts,
            "max_no_progress_repairs": no_progress_limit,
            "repair_failure_kind": failure_kind,
            "repair_failure_kind_attempt": attempts_by_kind[failure_kind],
            "current_failure_signature": failure_signature,
            "current_failure_summary": failure_status_summary(current_failure),
        }
        print(
            "  final repair: codex exec for actionable findings "
            f"({failure_kind} {attempts_by_kind[failure_kind]}/"
            f"{repair_limit_label(repair_attempts)}, "
            f"overall {repair_index}) -> run {relative_to_root(task_run_dir)}"
        )
        prompt_file = write_prompt_snapshot(task_run_dir, "final-repair-prompt.md", prompt)
        exec_log = task_run_dir / "codex-final-repair.log"
        print(f"    prompt: {relative_to_root(prompt_file)}")
        code = run_logged(
            command,
            exec_log,
            prompt,
            timeout_seconds=normalize_timeout_seconds(args.final_repair_timeout_seconds),
            status_file=CURRENT_RUN_PATH,
            status={
                **base_status,
                "stage": "final_repair",
                "agent": AGENT_CODEX,
                "prompt_file": relative_to_root(prompt_file),
            },
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
            if failure_is_actionable(gate_result):
                current_failure = gate_result
                print("  repair: gate failure is actionable; continuing focused repair loop")
                continue
            return False

        removed_artifacts = cleanup_generated_python_artifacts()
        if removed_artifacts:
            print(f"  cleanup: removed {removed_artifacts} generated Python cache dir(s)")

        prompt_pack_ready_for_current_scope = False
        if not args.skip_review and not audit_failure_is_actionable(current_failure):
            is_repair_verification = review_failure_is_actionable(current_failure)
            review_timeout_seconds = normalize_timeout_seconds(
                args.repair_review_timeout_seconds
                if is_repair_verification
                else args.review_timeout_seconds
            )
            if not is_repair_verification:
                prompt_pack = ensure_prompt_pack_for_scope(
                    task,
                    task_run_dir,
                    args,
                    base_status,
                    prompt_pack,
                )
                prompt_pack_ready_for_current_scope = True
            prompt_text = (
                repair_review_prompt(task, current_failure)
                if is_repair_verification
                else cast(dict[str, Any], prompt_pack)["review_prompt"]
            )
            command_label = (
                "codex repair verification" if is_repair_verification else "codex generated review"
            )
            prompt_name = (
                "repair-review-prompt.md"
                if is_repair_verification
                else "generated-review-prompt.md"
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
                prompt_name=prompt_name,
            )
            if not review_ok:
                print(f"  failed: {review_result}")
                if decision_failure_is_actionable(review_result):
                    current_failure = review_result
                    print("  repair: review finding remains actionable; continuing repair loop")
                    continue
                return False
            print(f"  {review_result}")

        if not args.skip_audit:
            is_repair_audit = audit_failure_is_actionable(current_failure)
            audit_timeout_seconds = normalize_timeout_seconds(
                args.repair_review_timeout_seconds
                if is_repair_audit
                else args.review_timeout_seconds
            )
            if not is_repair_audit and not prompt_pack_ready_for_current_scope:
                prompt_pack = ensure_prompt_pack_for_scope(
                    task,
                    task_run_dir,
                    args,
                    base_status,
                    prompt_pack,
                )
                prompt_pack_ready_for_current_scope = True
            audit_prompt_text = (
                repair_audit_prompt(task, current_failure)
                if is_repair_audit
                else cast(dict[str, Any], prompt_pack)["audit_prompt"]
            )
            command_label = "codex repair audit" if is_repair_audit else "codex generated audit"
            audit_ok, audit_result = run_audit(
                task,
                task_run_dir,
                args.codex_bin,
                args.codex_lean,
                audit_timeout_seconds,
                normalize_log_limit_bytes(args.review_log_limit_bytes),
                base_status,
                args.heartbeat_seconds,
                prompt_text=audit_prompt_text,
                command_label=command_label,
                prompt_name=(
                    "repair-audit-prompt.md" if is_repair_audit else "generated-audit-prompt.md"
                ),
            )
            if not audit_ok:
                print(f"  failed: {audit_result}")
                if audit_failure_is_actionable(audit_result):
                    current_failure = audit_result
                    print("  repair: audit finding remains actionable; continuing repair loop")
                    continue
                return False
            print(f"  {audit_result}")

            if not is_repair_audit and prompt_pack is not None:
                extra_ok, extra_result = run_extra_audits(
                    task,
                    task_run_dir,
                    args,
                    prompt_pack,
                    base_status,
                )
                if not extra_ok:
                    print(f"  failed: {extra_result}")
                    if audit_failure_is_actionable(extra_result):
                        current_failure = extra_result
                        print(
                            "  repair: extra audit finding remains actionable; "
                            "continuing repair loop"
                        )
                        continue
                    return False

        complete_task(ledger, task, task_run_dir, args.commit)
        if prompt_pack is not None:
            remember_pause_reasons(args, task, prompt_pack)
        write_static_run_state(
            CURRENT_RUN_PATH,
            base_status,
            status="complete",
            stage="complete",
            message=f"completed {task['id']} after final repair",
        )
        print(f"  complete: {task['id']}")
        return True


def block_task(
    task: dict[str, Any],
    base_status: dict[str, Any],
    message: str,
    *,
    stop_reason: str | None = None,
    failure_summary: str | None = None,
) -> None:
    print(f"blocked: {task['id']}")
    extra_status: dict[str, Any] = {}
    if stop_reason:
        extra_status["stop_reason"] = stop_reason
    if failure_summary:
        extra_status["current_failure_summary"] = failure_summary
    write_static_run_state(
        CURRENT_RUN_PATH,
        {**base_status, **extra_status},
        status="blocked",
        stage="blocked",
        message=message,
    )


def last_repair_block_message(args: argparse.Namespace, fallback: str) -> str:
    return str(getattr(args, "last_block_message", fallback))


def last_repair_stop_reason(args: argparse.Namespace) -> str | None:
    stop_reason = getattr(args, "last_stop_reason", None)
    return stop_reason if isinstance(stop_reason, str) else None


def last_repair_failure_summary(args: argparse.Namespace) -> str | None:
    summary = getattr(args, "last_failure_summary", None)
    return summary if isinstance(summary, str) else None


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

    verified_gates, prior_verification_path = prior_verified_gates(
        ledger,
        getattr(args, "prior_verification_file", None),
    )
    if verified_gates:
        print(
            "  prior verification: reusing "
            f"{', '.join(sorted(verified_gates))} from "
            f"{relative_to_root(cast(Path, prior_verification_path))}"
        )

    gates_ok, gate_result = run_quality_gates(
        task_run_dir,
        ledger,
        base_status,
        args.heartbeat_seconds,
        verified_gates=verified_gates,
        prior_verification_path=prior_verification_path,
    )
    if not gates_ok:
        print(f"  failed: {gate_result}")
        if (
            args.final_repair
            and failure_is_actionable(gate_result)
            and run_final_repair(ledger, task, args, gate_result)
        ):
            return True
        block_task(
            task,
            base_status,
            last_repair_block_message(args, "finish blocked by quality gate failure"),
            stop_reason=last_repair_stop_reason(args),
            failure_summary=last_repair_failure_summary(args),
        )
        print(gate_result)
        return False

    removed_artifacts = cleanup_generated_python_artifacts()
    if removed_artifacts:
        print(f"  cleanup: removed {removed_artifacts} generated Python cache dir(s)")

    prompt_pack = prepare_prompt_pack(task, task_run_dir, args, base_status)

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
            prompt_text=prompt_pack["review_prompt"],
            command_label="codex generated review",
            prompt_name="generated-review-prompt.md",
        )
        if not review_ok:
            print(f"  failed: {review_result}")
            if (
                args.final_repair
                and review_failure_is_actionable(review_result)
                and run_final_repair(ledger, task, args, review_result, prompt_pack)
            ):
                return True
            block_task(
                task,
                base_status,
                last_repair_block_message(args, "finish blocked by review failure"),
                stop_reason=last_repair_stop_reason(args),
                failure_summary=last_repair_failure_summary(args),
            )
            print(review_result)
            return False
        print(f"  {review_result}")

    if not args.skip_audit:
        audit_ok, audit_result = run_audit(
            task,
            task_run_dir,
            args.codex_bin,
            args.codex_lean,
            normalize_timeout_seconds(args.review_timeout_seconds),
            normalize_log_limit_bytes(args.review_log_limit_bytes),
            base_status,
            args.heartbeat_seconds,
            prompt_text=prompt_pack["audit_prompt"],
            command_label="codex generated audit",
            prompt_name="generated-audit-prompt.md",
        )
        if not audit_ok:
            print(f"  failed: {audit_result}")
            if (
                args.final_repair
                and audit_failure_is_actionable(audit_result)
                and run_final_repair(ledger, task, args, audit_result, prompt_pack)
            ):
                return True
            block_task(
                task,
                base_status,
                last_repair_block_message(args, "finish blocked by audit failure"),
                stop_reason=last_repair_stop_reason(args),
                failure_summary=last_repair_failure_summary(args),
            )
            print(audit_result)
            return False
        print(f"  {audit_result}")

        extra_ok, extra_result = run_extra_audits(
            task,
            task_run_dir,
            args,
            prompt_pack,
            base_status,
        )
        if not extra_ok:
            print(f"  failed: {extra_result}")
            if (
                args.final_repair
                and audit_failure_is_actionable(extra_result)
                and run_final_repair(ledger, task, args, extra_result, prompt_pack)
            ):
                return True
            block_task(
                task,
                base_status,
                last_repair_block_message(args, "finish blocked by extra audit failure"),
                stop_reason=last_repair_stop_reason(args),
                failure_summary=last_repair_failure_summary(args),
            )
            print(extra_result)
            return False

    complete_task(ledger, task, task_run_dir, args.commit)
    remember_pause_reasons(args, task, prompt_pack)
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
    lean = getattr(args, "codex_lean", False)
    label = "implementation (lean)" if lean else "implementation"
    command = [*codex_exec_command(args.codex_bin, "workspace-write", lean=lean), "-"]
    return label, command


def implementation_agent_invocation(
    args: argparse.Namespace,
    prompt: str,
) -> tuple[str, list[str], str | None, list[str]]:
    agent_label, command = implementation_agent_command(args)
    return agent_label, command, prompt, command


def task_allows_controller_changes(task: dict[str, Any]) -> bool:
    haystack_parts = [
        str(task.get("id", "")),
        str(task.get("title", "")),
        str(task.get("prompt", "")),
    ]
    with suppress(KeyError, OSError):
        haystack_parts.append(task_prompt_text(task))
    haystack = "\n".join(haystack_parts).lower()
    return any(
        token in haystack
        for token in (
            "automation",
            "task loop",
            "task-loop",
            "run_task_loop",
            "controller",
            "prompt-pack",
            "review-decision.schema",
        )
    )


def protected_path_violations(task: dict[str, Any], paths: list[str]) -> list[str]:
    if task_allows_controller_changes(task):
        return []
    violations: list[str] = []
    for path in paths:
        if any(
            path == protected or path.startswith(protected)
            for protected in PROTECTED_NON_AUTOMATION_PATHS
        ):
            violations.append(path)
    return sorted(set(violations))


def protected_status_entries(task: dict[str, Any]) -> list[tuple[str, str]]:
    if task_allows_controller_changes(task):
        return []
    return [
        (code, path)
        for code, path in git_status_entries()
        if any(
            path == protected or path.startswith(protected)
            for protected in PROTECTED_NON_AUTOMATION_PATHS
        )
    ]


def restore_tracked_protected_churn(task: dict[str, Any]) -> list[str]:
    entries = protected_status_entries(task)
    if not entries:
        return []

    blocking = sorted(
        {
            path
            for code, path in entries
            if code == "??" or "A" in code or "R" in code or "C" in code
        }
    )
    if blocking:
        formatted = "\n".join(f"- {path}" for path in blocking)
        raise LoopError(
            "Task diff created new, copied, or renamed task-loop controller files, but this is "
            "not an automation task. Commit or remove those files separately before completing "
            f"the task:\n{formatted}"
        )

    restorable = sorted({path for _code, path in entries})
    staged = sorted({path for code, path in entries if code[0] != " "})
    if staged:
        subprocess.run(["git", "restore", "--staged", "--", *staged], cwd=ROOT, check=True)
    subprocess.run(["git", "restore", "--", *restorable], cwd=ROOT, check=True)
    print("  protected cleanup: restored out-of-scope controller file(s)")
    for path in restorable:
        print(f"    - {path}")
    return restorable


def prepare_task_diff_for_completion(task: dict[str, Any]) -> None:
    restore_tracked_protected_churn(task)
    violations = protected_path_violations(task, git_status_paths())
    if violations:
        formatted = "\n".join(f"- {path}" for path in violations)
        raise LoopError(
            "Task diff touches task-loop controller files, but this is not an automation task. "
            "Commit or revert those files separately before completing the task:\n"
            f"{formatted}"
        )


def commit_task(task: dict[str, Any]) -> None:
    prepare_task_diff_for_completion(task)
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
    message += (
        "Tested: make fmt; make lint; make typecheck; make test; "
        "generated Codex review; generated Codex audit.\n"
    )
    message += "Not-tested: None.\n"
    subprocess.run(["git", "commit", "-m", message], cwd=ROOT, check=True)


def complete_task(
    ledger: dict[str, Any],
    task: dict[str, Any],
    task_run_dir: Path,
    commit: bool,
) -> None:
    if commit:
        prepare_task_diff_for_completion(task)
    task["status"] = "complete"
    task["completed_at"] = utc_now()
    task["last_run_dir"] = str(task_run_dir.relative_to(ROOT))
    save_ledger(ledger)
    if commit:
        commit_task(task)


def state_output_last_message_path(state: dict[str, Any]) -> Path | None:
    command = state.get("command")
    if not isinstance(command, list):
        return None
    try:
        index = command.index("--output-last-message")
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    value = command[index + 1]
    if not isinstance(value, str):
        return None
    return resolve_root_relative_path(value)


def state_run_dir(state: dict[str, Any]) -> Path | None:
    value = state.get("run_dir")
    if not isinstance(value, str) or not value:
        return None
    return resolve_root_relative_path(value)


def decision_file_passed(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        decision = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(decision, dict) and decision.get("decision") == "pass"


def state_decision_failure(state: dict[str, Any]) -> str | None:
    output_path = state_output_last_message_path(state)
    if output_path is None:
        return None
    try:
        decision = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(decision, dict) or decision.get("decision") == "pass":
        return None

    stage = str(state.get("stage") or "").lower()
    command_label = str(state.get("command_label") or "").lower()
    output_name = output_path.name.lower()
    label = (
        DECISION_LABEL_AUDIT
        if "audit" in stage or "audit" in command_label or "audit" in output_name
        else DECISION_LABEL_REVIEW
    )
    failure = format_decision_failure(label, output_path, decision)
    return failure if decision_failure_is_actionable(failure) else None


def current_diff_fingerprint_matches_state(task: dict[str, Any], state: dict[str, Any]) -> bool:
    restore_tracked_protected_churn(task)
    previous_fingerprint = state.get("git_non_protected_fingerprint")
    if isinstance(previous_fingerprint, str) and previous_fingerprint:
        return git_worktree_fingerprint(exclude_protected=True) == previous_fingerprint

    try:
        current_entries = git_status_entries()
    except LoopError:
        return False
    current_paths = product_status_paths_from_entries(task, current_entries)
    git_status = state.get("git_status")
    if not isinstance(git_status, dict) or git_status.get("truncated") is True:
        return False
    previous_files = git_status.get("files")
    if not isinstance(previous_files, list) or not all(
        isinstance(item, str) for item in previous_files
    ):
        return False
    previous_paths = product_status_paths_from_lines(task, cast(list[str], previous_files))
    return bool(current_paths) and current_paths == previous_paths


def prompt_pack_from_state_run_dir(
    state: dict[str, Any],
    status_snapshot: str,
) -> dict[str, Any] | None:
    run_dir = state_run_dir(state)
    if run_dir is None:
        return None
    prompt_pack_path = run_dir / "prompt-pack.json"
    try:
        raw_prompt_pack = json.loads(prompt_pack_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw_prompt_pack, dict):
        return None
    try:
        prompt_pack = validate_prompt_pack(raw_prompt_pack)
    except LoopError:
        return None
    prompt_pack["_scope_files"] = sorted(scope_files_from_snapshot(status_snapshot))
    return prompt_pack


def run_summary_has_successful_quality_gates(
    run_dir: Path | None,
    ledger: dict[str, Any],
) -> bool:
    required_gates = set(cast(list[str], ledger.get("quality_gates", [])))
    if not required_gates:
        return True
    if run_dir is None:
        return False
    summary_path = run_dir / "run-summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    stages = summary.get("stages") if isinstance(summary, dict) else None
    if not isinstance(stages, list):
        return False
    passed_gates = {
        stage.get("command_label")
        for stage in stages
        if isinstance(stage, dict)
        and stage.get("stage") == "quality_gate"
        and stage.get("status") == "succeeded"
        and stage.get("exit_code") == 0
    }
    return required_gates.issubset(passed_gates)


def current_scope_matches_successful_state(task: dict[str, Any], state: dict[str, Any]) -> bool:
    return current_diff_fingerprint_matches_state(task, state)


def fast_forward_completed_current_run(
    ledger: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    state = read_current_run_state()
    if state is None:
        return False
    if state.get("task_id") != task["id"]:
        return False
    if state.get("status") != "succeeded" or state.get("stage") != "audit":
        return False
    if state.get("exit_code") != 0:
        return False

    run_dir = state_run_dir(state)
    if not decision_file_passed(state_output_last_message_path(state)):
        return False
    if not run_summary_has_successful_quality_gates(run_dir, ledger):
        return False
    if not current_scope_matches_successful_state(task, state):
        return False

    task_run_dir = run_dir or RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-fast-forward"
    print("  fast-forward: reusing prior successful gates and audit for unchanged diff")
    complete_task(ledger, task, task_run_dir, args.commit)

    prompt_pack = {
        "requires_human_pause": False,
        "pause_reasons": [],
    }
    args.last_pause_reasons = pause_reasons_for_task(
        task,
        prompt_pack,
        review_scope_snapshot(),
    )
    if args.last_pause_reasons:
        print("  pause: human verification recommended before the next task")
        for reason in args.last_pause_reasons:
            print(f"    - {reason}")

    write_static_run_state(
        CURRENT_RUN_PATH,
        {
            "task_id": task["id"],
            "task_title": task["title"],
            "run_dir": relative_to_root(task_run_dir),
            "fast_forward": True,
        },
        status="complete",
        stage="complete",
        message=f"completed {task['id']} by reusing prior successful audit",
    )
    print(f"  complete: {task['id']}")
    return True


def resume_actionable_current_run(
    ledger: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
) -> bool | None:
    state = read_current_run_state()
    if state is None:
        return None
    if state.get("task_id") != task["id"]:
        return None
    if state.get("status") != "blocked":
        return None

    previous_failure = state_decision_failure(state)
    if previous_failure is None:
        return None
    if not current_diff_fingerprint_matches_state(task, state):
        print("  resume: prior actionable finding is stale for the current diff")
        return None

    status_snapshot = review_scope_snapshot()
    prompt_pack = prompt_pack_from_state_run_dir(state, status_snapshot)
    if prompt_pack is not None:
        print("  resume: continuing focused repair from prior actionable finding")
    else:
        print("  resume: continuing focused repair from prior actionable finding without pack")
    return run_final_repair(ledger, task, args, previous_failure, prompt_pack)


def run_task(
    ledger: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    print(f"task: {task['id']} - {task['title']}")
    previous_failure: str | None = None
    prompt_pack: dict[str, Any] | None = None
    agent_timeout_seconds = normalize_timeout_seconds(args.agent_timeout_seconds)
    review_timeout_seconds = normalize_timeout_seconds(args.review_timeout_seconds)
    agent_log_limit_bytes = normalize_log_limit_bytes(args.agent_log_limit_bytes)
    review_log_limit_bytes = normalize_log_limit_bytes(args.review_log_limit_bytes)
    for attempt in range(1, args.max_attempts + 1):
        pass_label = implementation_pass_label(attempt, args.max_attempts, previous_failure)
        task_run_dir = (
            RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-"
            f"{implementation_run_suffix(attempt, args.max_attempts)}"
        )
        prompt = implementation_prompt(task, attempt, previous_failure, pass_label)
        agent_label, command, input_text, logged_command = implementation_agent_invocation(
            args,
            prompt,
        )
        base_status = {
            "task_id": task["id"],
            "task_title": task["title"],
            "attempt": attempt,
            "max_attempts": args.max_attempts,
            "pass_label": pass_label,
            "run_dir": relative_to_root(task_run_dir),
        }
        initial_status_lines = git_status_lines()
        print(f"  {pass_label}: {agent_label} -> run {relative_to_root(task_run_dir)}")
        prompt_file = write_prompt_snapshot(
            task_run_dir,
            "codex-implementation-prompt.md",
            prompt,
        )
        exec_log = task_run_dir / "codex-exec.log"
        print(f"    prompt: {relative_to_root(prompt_file)}")
        print(f"    log: {relative_to_root(exec_log)}")
        code = run_logged(
            command,
            exec_log,
            input_text,
            timeout_seconds=agent_timeout_seconds,
            status_file=CURRENT_RUN_PATH,
            status={
                **base_status,
                "stage": "implementation",
                "agent": AGENT_CODEX,
                "prompt_file": relative_to_root(prompt_file),
            },
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

        removed_artifacts = cleanup_generated_python_artifacts()
        if removed_artifacts:
            print(f"  cleanup: removed {removed_artifacts} generated Python cache dir(s)")

        prompt_pack = prepare_prompt_pack(task, task_run_dir, args, base_status)

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
                prompt_text=prompt_pack["review_prompt"],
                command_label="codex generated review",
                prompt_name="generated-review-prompt.md",
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

        if not args.skip_audit:
            audit_ok, audit_result = run_audit(
                task,
                task_run_dir,
                args.codex_bin,
                args.codex_lean,
                review_timeout_seconds,
                review_log_limit_bytes,
                base_status,
                args.heartbeat_seconds,
                prompt_text=prompt_pack["audit_prompt"],
                command_label="codex generated audit",
                prompt_name="generated-audit-prompt.md",
            )
            if not audit_ok:
                previous_failure = audit_result
                print(f"  failed: {audit_result}")
                if not audit_failure_is_actionable(audit_result):
                    write_static_run_state(
                        CURRENT_RUN_PATH,
                        base_status,
                        status="blocked",
                        stage="audit",
                        message=(
                            "audit gate did not produce actionable findings; "
                            "stopped before a repair attempt"
                        ),
                    )
                    return False
                break
            print(f"  {audit_result}")

            extra_ok, extra_result = run_extra_audits(
                task,
                task_run_dir,
                args,
                prompt_pack,
                base_status,
            )
            if not extra_ok:
                previous_failure = extra_result
                print(f"  failed: {extra_result}")
                if not audit_failure_is_actionable(extra_result):
                    write_static_run_state(
                        CURRENT_RUN_PATH,
                        base_status,
                        status="blocked",
                        stage="audit",
                        message=(
                            "extra audit gate did not produce actionable findings; "
                            "stopped before a repair attempt"
                        ),
                    )
                    return False
                break

        complete_task(ledger, task, task_run_dir, args.commit)
        remember_pause_reasons(args, task, prompt_pack)
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
        and run_final_repair(ledger, task, args, previous_failure, prompt_pack)
    ):
        return True

    stop_reason = getattr(args, "last_stop_reason", "unknown")
    block_message = getattr(
        args,
        "last_block_message",
        f"blocked after {args.max_attempts} implementation attempt(s)",
    )
    print(f"blocked: {task['id']} - {block_message}")
    write_static_run_state(
        CURRENT_RUN_PATH,
        {
            "task_id": task["id"],
            "task_title": task["title"],
            "attempt": args.max_attempts,
            "max_attempts": args.max_attempts,
            "stop_reason": stop_reason,
            "current_failure_summary": getattr(args, "last_failure_summary", None),
        },
        status="blocked",
        stage="blocked",
        message=block_message,
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
        help="Maximum runtime for prompt-pack generation and review/audit gates; 0 disables.",
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
        help="Maximum prompt-pack/review/audit log size before stopping; 0 disables.",
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
        help="Maximum runtime for focused post-repair review/audit gates; 0 disables.",
    )
    finish_parser.add_argument(
        "--final-repair-attempts",
        type=int,
        default=DEFAULT_FINAL_REPAIR_ATTEMPTS,
        help=(
            "Maximum focused Codex repair attempts after actionable gate/review/audit findings; "
            "0 means no fixed cap."
        ),
    )
    finish_parser.add_argument(
        "--max-no-progress-repairs",
        type=int,
        default=DEFAULT_NO_PROGRESS_REPAIR_LIMIT,
        help=(
            "Stop after this many repeated repair cycles with the same actionable finding and "
            "unchanged worktree fingerprint; 0 disables this guard."
        ),
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
        help="Allow gates/review/audit even when there is no existing implementation diff.",
    )
    finish_parser.add_argument(
        "--prior-verification-file",
        help=(
            "Reuse quality gates explicitly shown as passed in this file; "
            "gates without evidence still run."
        ),
    )
    finish_parser.add_argument("--skip-review", action="store_true")
    finish_parser.add_argument("--skip-audit", action="store_true")
    finish_parser.add_argument(
        "--skip-prompt-pack",
        action="store_true",
        help="Use deterministic built-in review/audit prompts instead of generated prompts.",
    )
    finish_parser.add_argument(
        "--pause-policy",
        choices=("auto", "never", "always"),
        default="auto",
        help="When to pause before automatically continuing to another task.",
    )
    finish_parser.add_argument("--codex-bin", default="codex")
    finish_parser.add_argument(
        "--codex-full-config",
        dest="codex_lean",
        action="store_false",
        help="Load full Codex user config; this is the default.",
    )
    finish_parser.add_argument(
        "--codex-lean",
        dest="codex_lean",
        action="store_true",
        help="Use lean Codex exec without user config for lower overhead.",
    )
    finish_parser.set_defaults(codex_lean=False)

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
        help="Maximum runtime for prompt-pack generation and review/audit gates; 0 disables.",
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
        help="Maximum prompt-pack/review/audit log size before stopping; 0 disables.",
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
        help="Maximum runtime for focused post-repair review/audit gates; 0 disables.",
    )
    run_parser.add_argument(
        "--final-repair-attempts",
        type=int,
        default=DEFAULT_FINAL_REPAIR_ATTEMPTS,
        help=(
            "Maximum focused Codex repair attempts after actionable gate/review/audit findings; "
            "0 means no fixed cap."
        ),
    )
    run_parser.add_argument(
        "--max-no-progress-repairs",
        type=int,
        default=DEFAULT_NO_PROGRESS_REPAIR_LIMIT,
        help=(
            "Stop after this many repeated repair cycles with the same actionable finding and "
            "unchanged worktree fingerprint; 0 disables this guard."
        ),
    )
    run_parser.add_argument(
        "--no-final-repair",
        dest="final_repair",
        action="store_false",
        help="Disable the bounded Codex repair pass after actionable findings.",
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
        help="Allow gates/review/audit even when a clean implementation pass produces no diff.",
    )
    run_parser.add_argument("--skip-review", action="store_true")
    run_parser.add_argument("--skip-audit", action="store_true")
    run_parser.add_argument(
        "--skip-prompt-pack",
        action="store_true",
        help="Use deterministic built-in review/audit prompts instead of generated prompts.",
    )
    run_parser.add_argument(
        "--pause-policy",
        choices=("auto", "never", "always"),
        default="auto",
        help="When to pause before automatically continuing to another task.",
    )
    run_parser.add_argument(
        "--codex",
        action="store_true",
        help="Deprecated no-op; Codex is the only implementation agent.",
    )
    run_parser.add_argument("--codex-bin", default="codex")
    run_parser.add_argument(
        "--codex-full-config",
        dest="codex_lean",
        action="store_false",
        help="Load full Codex user config; this is the default.",
    )
    run_parser.add_argument(
        "--codex-lean",
        dest="codex_lean",
        action="store_true",
        help="Use lean Codex exec without user config for lower overhead.",
    )
    run_parser.set_defaults(codex_lean=False)
    args = parser.parse_args()
    apply_agent_defaults(args)
    return args


def apply_agent_defaults(args: argparse.Namespace) -> None:
    if args.command != "run":
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
    validate_non_negative_int(args.final_repair_attempts, "final repair attempts")
    validate_non_negative_int(args.max_no_progress_repairs, "max no-progress repairs")
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

    completed_count = 0
    current_status_lines = git_status_lines()
    if current_status_lines is None:
        raise LoopError("Unable to inspect git status before starting task loop.")
    if current_status_lines and not args.allow_dirty:
        resume_task = resumable_dirty_task(ledger, args)
        if resume_task is None:
            raise LoopError(
                "Worktree is dirty and no unfinished task-loop candidate was found in "
                ".task-runs/current.json. Commit, stash, or run "
                "`python3 scripts/run_task_loop.py finish --task <task-id> --commit` "
                "when the dirty diff intentionally belongs to one task."
            )
        print(f"dirty worktree: resuming unfinished {resume_task['id']}")
        if fast_forward_completed_current_run(ledger, resume_task, args):
            pass
        else:
            resumed_repair = resume_actionable_current_run(ledger, resume_task, args)
            if resumed_repair is True:
                pass
            elif resumed_repair is False:
                return 1
            else:
                print("  resume: prior success is not reusable; running finish lane")
                if not run_finish_task(ledger, resume_task, args):
                    return 1
        completed_count += 1
        pause_reasons = getattr(args, "last_pause_reasons", [])
        if pause_reasons:
            print("paused: human verification recommended before continuing to the next task")
            return 0
        remaining_status_lines = git_status_lines()
        if remaining_status_lines is None:
            raise LoopError("Unable to inspect git status after finishing resumed task.")
        if remaining_status_lines:
            raise LoopError(
                "Finished the resumed task, but the worktree is still dirty. "
                "Use --commit for continuous runs, or commit/stash the remaining diff "
                "before starting the next task."
            )
        if args.task or (args.limit != 0 and completed_count >= args.limit):
            return 0

    if args.task:
        candidates = [task_map(ledger)[args.task]]
    else:
        cluster, candidates = pending_task_selection(ledger, args.cluster)
        if cluster and not args.cluster:
            ledger["active_cluster"] = cluster["id"]
    if not candidates:
        print("No pending tasks.")
        return 0
    remaining_limit = args.limit if args.limit == 0 else max(0, args.limit - completed_count)
    selected = candidates if remaining_limit == 0 else candidates[:remaining_limit]
    for task in selected:
        if not run_task(ledger, task, args):
            return 1
        pause_reasons = getattr(args, "last_pause_reasons", [])
        if pause_reasons:
            print("paused: human verification recommended before continuing to the next task")
            return 0
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LoopError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
