from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def load_task_loop() -> ModuleType:
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "run_task_loop.py"
    spec = importlib.util.spec_from_file_location("run_task_loop", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASK_LOOP = load_task_loop()


def test_default_pending_tasks_advance_past_completed_active_cluster() -> None:
    ledger = {
        "active_cluster": "P0-foundations-finish",
        "clusters": [
            {
                "id": "P0-foundations-finish",
                "description": "Complete foundation tasks.",
                "tasks": ["P0-05"],
            },
            {
                "id": "P1-ingestion-mvp",
                "description": "Build ingestion tasks.",
                "tasks": ["P1-01"],
            },
        ],
        "tasks": [
            {"id": "P0-05", "status": "complete"},
            {"id": "P1-01", "status": "pending"},
        ],
    }

    tasks = TASK_LOOP.pending_tasks(ledger, cluster_id=None)

    assert [task["id"] for task in tasks] == ["P1-01"]


def test_explicit_completed_cluster_does_not_advance_to_next_cluster() -> None:
    ledger = {
        "active_cluster": "P0-foundations-finish",
        "clusters": [
            {
                "id": "P0-foundations-finish",
                "description": "Complete foundation tasks.",
                "tasks": ["P0-05"],
            },
            {
                "id": "P1-ingestion-mvp",
                "description": "Build ingestion tasks.",
                "tasks": ["P1-01"],
            },
        ],
        "tasks": [
            {"id": "P0-05", "status": "complete"},
            {"id": "P1-01", "status": "pending"},
        ],
    }

    tasks = TASK_LOOP.pending_tasks(ledger, cluster_id="P0-foundations-finish")

    assert tasks == []


def test_run_command_defaults_to_four_attempts(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "run"])

    args = TASK_LOOP.parse_args()

    assert args.max_attempts == 4
    assert args.agent_timeout_seconds == 3600
    assert args.review_timeout_seconds == 1800
    assert args.agent == "codex"


def test_run_command_accepts_disabled_timeouts(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_task_loop.py",
            "run",
            "--agent-timeout-seconds",
            "0",
            "--review-timeout-seconds",
            "0",
        ],
    )

    args = TASK_LOOP.parse_args()

    assert TASK_LOOP.normalize_timeout_seconds(args.agent_timeout_seconds) is None
    assert TASK_LOOP.normalize_timeout_seconds(args.review_timeout_seconds) is None


def test_current_command_is_available(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "current"])

    args = TASK_LOOP.parse_args()

    assert args.command == "current"
    assert args.watch is False
    assert args.interval == 2.0


def test_current_command_accepts_watch_options(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_task_loop.py",
            "current",
            "--watch",
            "--interval",
            "1.5",
            "--tail-lines",
            "80",
        ],
    )

    args = TASK_LOOP.parse_args()

    assert args.command == "current"
    assert args.watch is True
    assert args.interval == 1.5
    assert args.tail_lines == 80


def test_negative_timeout_is_rejected() -> None:
    try:
        TASK_LOOP.normalize_timeout_seconds(-1)
    except TASK_LOOP.LoopError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative timeout should fail")


def test_negative_heartbeat_is_rejected() -> None:
    try:
        TASK_LOOP.validate_heartbeat_seconds(-1)
    except TASK_LOOP.LoopError as exc:
        assert "Heartbeat seconds" in str(exc)
    else:
        raise AssertionError("negative heartbeat should fail")


def test_nonpositive_watch_options_are_rejected() -> None:
    try:
        TASK_LOOP.validate_positive_float(0, "watch interval")
    except TASK_LOOP.LoopError as exc:
        assert "watch interval" in str(exc)
    else:
        raise AssertionError("zero watch interval should fail")

    try:
        TASK_LOOP.validate_positive_int(0, "tail lines")
    except TASK_LOOP.LoopError as exc:
        assert "tail lines" in str(exc)
    else:
        raise AssertionError("zero tail lines should fail")


def test_run_command_selects_kimi_agent(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "run", "--kimi"])

    args = TASK_LOOP.parse_args()

    assert args.agent == "kimi"
    assert args.max_attempts == 2
    assert args.agent_timeout_seconds == 1200
    assert args.review_timeout_seconds == 600


def test_kimi_run_command_accepts_explicit_timeout_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_task_loop.py",
            "run",
            "--kimi",
            "--max-attempts",
            "3",
            "--agent-timeout-seconds",
            "0",
            "--review-timeout-seconds",
            "90",
        ],
    )

    args = TASK_LOOP.parse_args()

    assert args.agent == "kimi"
    assert args.max_attempts == 3
    assert TASK_LOOP.normalize_timeout_seconds(args.agent_timeout_seconds) is None
    assert args.review_timeout_seconds == 90


def test_kimi_implementation_command_uses_prompt_mode() -> None:
    args = SimpleNamespace(agent="kimi", kimi_bin="kimi")

    label, command = TASK_LOOP.implementation_agent_command(args)

    assert label == "kimi --prompt"
    assert command == ["kimi"]


def test_kimi_invocation_uses_noninteractive_prompt_mode() -> None:
    args = SimpleNamespace(agent="kimi", kimi_bin="kimi")

    label, command, input_text, logged_command = TASK_LOOP.implementation_agent_invocation(
        args,
        "do the task",
    )

    assert label == "kimi --prompt"
    assert command == ["kimi", "--prompt", "do the task"]
    assert input_text is None
    assert logged_command == ["kimi", "--prompt", "<task-prompt>"]


def test_kimi_initial_prompt_includes_focused_execution_contract() -> None:
    task = {
        "id": "P1-02",
        "title": "normalization module",
        "prompt": "tasks/P1-02-normalization-module.md",
    }

    prompt = TASK_LOOP.implementation_prompt(task, 1, None, agent="kimi")

    assert "Kimi-specific execution contract" in prompt
    assert "git status --short" in prompt
    assert "untracked files are part of the" in prompt


def test_kimi_repair_prompt_prioritizes_review_findings() -> None:
    task = {
        "id": "P1-02",
        "title": "normalization module",
        "prompt": "tasks/P1-02-normalization-module.md",
    }

    prompt = TASK_LOOP.implementation_prompt(
        task,
        2,
        "Review decision JSON: provenance links are wrong",
        agent="kimi",
    )

    assert "Kimi repair mode" in prompt
    assert "do not restart from broad PRD/repo discovery" in prompt
    assert "provenance links are wrong" in prompt


def test_codex_repair_prompt_prioritizes_failure_context() -> None:
    task = {
        "id": "P1-04",
        "title": "ios healthkit sync",
        "prompt": "tasks/P1-04-ios-healthkit-sync.md",
    }

    prompt = TASK_LOOP.implementation_prompt(
        task,
        2,
        "Review decision JSON: demo mode missing synthetic samples",
        agent="codex",
    )

    assert "Repair mode" in prompt
    assert "Do not restart broad repo discovery" in prompt
    assert "demo mode missing synthetic samples" in prompt


def test_review_prompt_is_bounded_to_task_and_changed_files() -> None:
    task = {
        "id": "P1-04",
        "title": "ios healthkit sync",
        "prompt": "tasks/P1-04-ios-healthkit-sync.md",
    }

    prompt = TASK_LOOP.review_prompt(task, status_snapshot="?? apps/ios/Sources/")

    assert "Use the task prompt below as the source of truth" in prompt
    assert "?? apps/ios/Sources/" in prompt
    assert "Do not run build or test commands" in prompt
    assert "Demo mode launches with synthetic data" in prompt


def test_codex_implementation_command_preserves_existing_exec_shape() -> None:
    args = SimpleNamespace(agent="codex", codex_bin="codex")

    label, command = TASK_LOOP.implementation_agent_command(args)

    assert label == "codex exec"
    assert command[:2] == ["codex", "exec"]
    assert "--sandbox" in command
    assert command[-1] == "-"


def test_codex_invocation_keeps_prompt_on_stdin() -> None:
    args = SimpleNamespace(agent="codex", codex_bin="codex")

    label, command, input_text, logged_command = TASK_LOOP.implementation_agent_invocation(
        args,
        "do the task",
    )

    assert label == "codex exec"
    assert command == logged_command
    assert input_text == "do the task"


def test_run_logged_times_out_stalled_command(tmp_path) -> None:
    log_file = tmp_path / "stalled.log"
    status_file = tmp_path / "current.json"

    code = TASK_LOOP.run_logged(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        log_file,
        timeout_seconds=1,
        status_file=status_file,
        status={
            "task_id": "P1-02",
            "task_title": "normalization module",
            "stage": "implementation",
            "attempt": 1,
            "max_attempts": 4,
            "run_dir": str(tmp_path),
        },
    )

    assert code == 124
    log = log_file.read_text(encoding="utf-8")
    assert "[timeout_seconds] 1" in log
    assert "[exit_code] 124" in log
    state = json.loads(status_file.read_text(encoding="utf-8"))
    assert state["status"] == "timed_out"
    assert state["task_id"] == "P1-02"
    assert state["log_file"].endswith("stalled.log")


def test_run_logged_writes_success_state(tmp_path) -> None:
    log_file = tmp_path / "ok.log"
    status_file = tmp_path / "current.json"

    code = TASK_LOOP.run_logged(
        [sys.executable, "-c", "print('done')"],
        log_file,
        status_file=status_file,
        status={"task_id": "P1-02", "stage": "implementation"},
        heartbeat_seconds=0,
    )

    assert code == 0
    state = json.loads(status_file.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["exit_code"] == 0
    assert state["command"][0] == sys.executable


def test_review_failure_context_includes_structured_decision(tmp_path) -> None:
    decision = {
        "decision": "fail",
        "summary": "Replay missed normalization repair.",
        "findings": [
            {
                "severity": "major",
                "file": "apps/api/baseline_api/api/v1/health.py",
                "line": 57,
                "message": "Retry should enqueue pending normalization.",
            }
        ],
        "residual_risk": "none",
    }
    output_file = tmp_path / "review-decision.json"
    output_file.write_text(json.dumps(decision), encoding="utf-8")

    context = TASK_LOOP.format_review_failure(output_file, decision)

    assert "Review decision JSON" in context
    assert "Replay missed normalization repair" in context
    assert "Retry should enqueue pending normalization" in context


def test_kimi_retries_only_actionable_structured_review_failures() -> None:
    actionable = "review failed; see file\n\nReview decision JSON:\n{}"
    non_actionable = "review command failed; see file\n\nLog tail:\nturn interrupted"

    assert TASK_LOOP.should_retry_after_review_failure("kimi", actionable) is True
    assert TASK_LOOP.should_retry_after_review_failure("kimi", non_actionable) is False
    assert TASK_LOOP.should_retry_after_review_failure("codex", non_actionable) is True
