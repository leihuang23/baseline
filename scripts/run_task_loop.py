#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
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
DEFAULT_FINAL_REPAIR_ATTEMPTS = 2
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
CODEX_REPAIR_GUIDANCE = """Repair mode:
- Treat the existing working tree as the previous attempt's draft.
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
    prompt_file = state.get("prompt_file")
    if isinstance(prompt_file, str):
        lines.append(f"prompt: {prompt_file}")
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


def implementation_prompt(
    task: dict[str, Any],
    attempt: int,
    previous_failure: str | None,
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


def run_final_repair(
    ledger: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
    previous_failure: str,
    prompt_pack: dict[str, Any] | None = None,
) -> bool:
    max_attempts = getattr(args, "max_attempts", 0)
    repair_attempts = getattr(args, "final_repair_attempts", DEFAULT_FINAL_REPAIR_ATTEMPTS)
    current_failure = previous_failure
    attempts_by_kind = {"gate": 0, "decision": 0}
    repair_index = 0
    while True:
        failure_kind = repair_failure_kind(current_failure)
        if failure_kind is None:
            print("  failed: final repair failure is not actionable")
            return False
        if attempts_by_kind[failure_kind] >= repair_attempts:
            print(f"  failed: exhausted {repair_attempts} {failure_kind} repair attempt(s)")
            return False
        attempts_by_kind[failure_kind] += 1
        repair_index += 1
        task_run_dir = (
            RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-final-repair-{repair_index}"
        )
        prompt = implementation_prompt(task, max_attempts + repair_index, current_failure)
        command = [
            *codex_exec_command(args.codex_bin, "workspace-write", lean=args.codex_lean),
            "-",
        ]
        base_status = {
            "task_id": task["id"],
            "task_title": task["title"],
            "attempt": max_attempts + repair_index,
            "max_attempts": max_attempts,
            "run_dir": relative_to_root(task_run_dir),
            "final_repair": True,
            "repair_attempt": repair_index,
            "final_repair_attempts": repair_attempts,
            "repair_failure_kind": failure_kind,
            "repair_failure_kind_attempt": attempts_by_kind[failure_kind],
        }
        print(
            "  final repair: codex exec for actionable findings "
            f"({failure_kind} {attempts_by_kind[failure_kind]}/{repair_attempts}, "
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

        needs_prompt_pack = (
            not args.skip_review or not args.skip_audit
        ) and not audit_failure_is_actionable(current_failure)
        if needs_prompt_pack:
            current_snapshot = review_scope_snapshot()
            if prompt_pack is None:
                prompt_pack = prepare_prompt_pack(
                    task,
                    task_run_dir,
                    args,
                    base_status,
                    current_snapshot,
                )
            elif prompt_pack_matches_scope(prompt_pack, current_snapshot):
                print("  prompt-pack: reusing existing generated review/audit prompts")
                write_prompt_pack_artifacts(task_run_dir, prompt_pack)
            else:
                print("  prompt-pack: changed-file scope expanded; regenerating prompts")
                prompt_pack = prepare_prompt_pack(
                    task,
                    task_run_dir,
                    args,
                    base_status,
                    current_snapshot,
                )

        if not args.skip_review and not audit_failure_is_actionable(current_failure):
            is_repair_verification = review_failure_is_actionable(current_failure)
            review_timeout_seconds = normalize_timeout_seconds(
                args.repair_review_timeout_seconds
                if is_repair_verification
                else args.review_timeout_seconds
            )
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
        block_task(task, base_status, "finish blocked by quality gate failure")
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
            block_task(task, base_status, "finish blocked by review failure")
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
            block_task(task, base_status, "finish blocked by audit failure")
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
            block_task(task, base_status, "finish blocked by extra audit failure")
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
    label = "codex exec (lean)" if lean else "codex exec"
    command = [*codex_exec_command(args.codex_bin, "workspace-write", lean=lean), "-"]
    return label, command


def implementation_agent_invocation(
    args: argparse.Namespace,
    prompt: str,
) -> tuple[str, list[str], str | None, list[str]]:
    agent_label, command = implementation_agent_command(args)
    return agent_label, command, prompt, command


def task_allows_controller_changes(task: dict[str, Any]) -> bool:
    haystack_parts = [task["id"], task["title"], str(task.get("prompt", ""))]
    with suppress(OSError):
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
        task_run_dir = RUNS_DIR / f"{utc_now().replace(':', '')}-{task['id']}-attempt-{attempt}"
        prompt = implementation_prompt(task, attempt, previous_failure)
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
        help="Maximum focused Codex repair attempts after actionable gate/review/audit findings.",
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
        help="Maximum focused Codex repair attempts after actionable gate/review/audit findings.",
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
    validate_positive_int(args.final_repair_attempts, "final repair attempts")
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
        print(f"dirty worktree: resuming unfinished {resume_task['id']} through the finish lane")
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
