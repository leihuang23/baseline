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


def test_run_command_defaults_to_one_attempt_with_bounded_review(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "run"])

    args = TASK_LOOP.parse_args()

    assert args.max_attempts == 1
    assert args.agent_timeout_seconds == 3600
    assert args.review_timeout_seconds == 600
    assert args.repair_review_timeout_seconds == 300
    assert args.final_repair_attempts == 2
    assert args.agent_log_limit_bytes == 0
    assert args.review_log_limit_bytes == 0
    assert args.codex_lean is False
    assert args.allow_no_changes is False
    assert args.skip_prompt_pack is False
    assert args.pause_policy == "auto"
    assert args.skip_audit is False


def test_run_command_accepts_disabled_timeouts_and_log_limits(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_task_loop.py",
            "run",
            "--agent-timeout-seconds",
            "0",
            "--review-timeout-seconds",
            "0",
            "--repair-review-timeout-seconds",
            "0",
            "--agent-log-limit-bytes",
            "0",
            "--review-log-limit-bytes",
            "0",
        ],
    )

    args = TASK_LOOP.parse_args()

    assert TASK_LOOP.normalize_timeout_seconds(args.agent_timeout_seconds) is None
    assert TASK_LOOP.normalize_timeout_seconds(args.review_timeout_seconds) is None
    assert TASK_LOOP.normalize_timeout_seconds(args.repair_review_timeout_seconds) is None
    assert TASK_LOOP.normalize_log_limit_bytes(args.agent_log_limit_bytes) is None
    assert TASK_LOOP.normalize_log_limit_bytes(args.review_log_limit_bytes) is None


def test_run_resumes_dirty_current_task_before_pending_queue(monkeypatch, tmp_path) -> None:
    ledger = {
        "active_cluster": "P4-memory-data-controls",
        "clusters": [
            {
                "id": "P4-memory-data-controls",
                "description": "Memory tasks.",
                "tasks": ["P4-01", "P4-02"],
            }
        ],
        "quality_gates": [],
        "tasks": [
            {"id": "P4-01", "status": "pending", "title": "memory daily weekly"},
            {"id": "P4-02", "status": "pending", "title": "memory monthly quarterly"},
        ],
    }
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps({"status": "blocked", "task_id": "P4-01", "pid": None}),
        encoding="utf-8",
    )
    status_results = [[" M apps/api/example.py"], []]
    calls: list[str] = []

    def fake_finish(_ledger, task, args) -> bool:
        calls.append(f"finish:{task['id']}:{args.commit}")
        task["status"] = "complete"
        return True

    def fake_run(_ledger, task, _args) -> bool:
        calls.append(f"run:{task['id']}")
        task["status"] = "complete"
        return True

    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "run", "--limit", "0", "--commit"])
    monkeypatch.setattr(TASK_LOOP, "CURRENT_RUN_PATH", current_path)
    monkeypatch.setattr(TASK_LOOP, "load_ledger", lambda: ledger)
    monkeypatch.setattr(
        TASK_LOOP,
        "git_status_lines",
        lambda: status_results.pop(0) if status_results else [],
    )
    monkeypatch.setattr(TASK_LOOP, "run_finish_task", fake_finish)
    monkeypatch.setattr(TASK_LOOP, "run_task", fake_run)

    assert TASK_LOOP.main() == 0

    assert calls == ["finish:P4-01:True", "run:P4-02"]


def test_run_fast_forwards_dirty_task_after_successful_audit(
    monkeypatch,
    tmp_path,
) -> None:
    ledger = {
        "active_cluster": "P4-memory-data-controls",
        "clusters": [
            {
                "id": "P4-memory-data-controls",
                "description": "Memory tasks.",
                "tasks": ["P4-01", "P4-02"],
            }
        ],
        "quality_gates": ["make test"],
        "tasks": [
            {"id": "P4-01", "status": "pending", "title": "memory daily weekly"},
            {"id": "P4-02", "status": "pending", "title": "memory monthly quarterly"},
        ],
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    decision_file = run_dir / "audit-decision.json"
    decision_file.write_text(json.dumps({"decision": "pass"}), encoding="utf-8")
    (run_dir / "run-summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stages": [
                    {
                        "stage": "quality_gate",
                        "command_label": "make test",
                        "status": "succeeded",
                        "exit_code": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps(
            {
                "status": "succeeded",
                "stage": "audit",
                "exit_code": 0,
                "task_id": "P4-01",
                "task_title": "memory daily weekly",
                "pid": None,
                "run_dir": str(run_dir),
                "command": ["codex", "--output-last-message", str(decision_file)],
                "git_status": {
                    "files": [" M apps/api/example.py"],
                    "truncated": False,
                },
            }
        ),
        encoding="utf-8",
    )
    status_results = [[" M apps/api/example.py"], []]
    calls: list[str] = []

    def fake_complete(_ledger, task, _run_dir, commit) -> None:
        calls.append(f"complete:{task['id']}:{commit}")
        task["status"] = "complete"

    def fake_run(_ledger, task, _args) -> bool:
        calls.append(f"run:{task['id']}")
        task["status"] = "complete"
        return True

    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "run", "--limit", "0", "--commit"])
    monkeypatch.setattr(TASK_LOOP, "CURRENT_RUN_PATH", current_path)
    monkeypatch.setattr(TASK_LOOP, "load_ledger", lambda: ledger)
    monkeypatch.setattr(
        TASK_LOOP,
        "git_status_lines",
        lambda: status_results.pop(0) if status_results else [],
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "git_status_entries",
        lambda: [(" M", "apps/api/example.py")],
    )
    monkeypatch.setattr(TASK_LOOP, "review_scope_snapshot", lambda: " M apps/api/example.py")
    monkeypatch.setattr(TASK_LOOP, "complete_task", fake_complete)
    monkeypatch.setattr(
        TASK_LOOP,
        "run_finish_task",
        lambda *args, **kwargs: calls.append("unexpected-finish") or False,
    )
    monkeypatch.setattr(TASK_LOOP, "run_task", fake_run)

    assert TASK_LOOP.main() == 0

    assert calls == ["complete:P4-01:True", "run:P4-02"]


def test_run_counts_resumed_dirty_task_toward_limit(monkeypatch, tmp_path) -> None:
    ledger = {
        "active_cluster": "P4-memory-data-controls",
        "clusters": [
            {
                "id": "P4-memory-data-controls",
                "description": "Memory tasks.",
                "tasks": ["P4-01", "P4-02"],
            }
        ],
        "quality_gates": [],
        "tasks": [
            {"id": "P4-01", "status": "pending", "title": "memory daily weekly"},
            {"id": "P4-02", "status": "pending", "title": "memory monthly quarterly"},
        ],
    }
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps({"status": "blocked", "task_id": "P4-01", "pid": None}),
        encoding="utf-8",
    )
    status_results = [[" M apps/api/example.py"], []]
    calls: list[str] = []

    def fake_finish(_ledger, task, _args) -> bool:
        calls.append(f"finish:{task['id']}")
        task["status"] = "complete"
        return True

    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "run", "--limit", "1", "--commit"])
    monkeypatch.setattr(TASK_LOOP, "CURRENT_RUN_PATH", current_path)
    monkeypatch.setattr(TASK_LOOP, "load_ledger", lambda: ledger)
    monkeypatch.setattr(
        TASK_LOOP,
        "git_status_lines",
        lambda: status_results.pop(0) if status_results else [],
    )
    monkeypatch.setattr(TASK_LOOP, "run_finish_task", fake_finish)
    monkeypatch.setattr(
        TASK_LOOP,
        "run_task",
        lambda *args, **kwargs: calls.append("unexpected-run") or True,
    )

    assert TASK_LOOP.main() == 0

    assert calls == ["finish:P4-01"]


def test_run_dirty_without_resumable_current_task_still_blocks(monkeypatch, tmp_path) -> None:
    ledger = {
        "active_cluster": "P4-memory-data-controls",
        "clusters": [
            {
                "id": "P4-memory-data-controls",
                "description": "Memory tasks.",
                "tasks": ["P4-01"],
            }
        ],
        "quality_gates": [],
        "tasks": [{"id": "P4-01", "status": "pending", "title": "memory daily weekly"}],
    }

    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "run", "--limit", "0", "--commit"])
    monkeypatch.setattr(TASK_LOOP, "CURRENT_RUN_PATH", tmp_path / "missing-current.json")
    monkeypatch.setattr(TASK_LOOP, "load_ledger", lambda: ledger)
    monkeypatch.setattr(TASK_LOOP, "git_status_lines", lambda: [" M apps/api/example.py"])

    try:
        TASK_LOOP.main()
    except TASK_LOOP.LoopError as exc:
        assert "no unfinished task-loop candidate" in str(exc)
    else:
        raise AssertionError("dirty run without current task should block")


def test_finish_command_accepts_prior_verification_file(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_task_loop.py",
            "finish",
            "--prior-verification-file",
            ".task-runs/app-verification.txt",
        ],
    )

    args = TASK_LOOP.parse_args()

    assert args.command == "finish"
    assert args.prior_verification_file == ".task-runs/app-verification.txt"


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


def test_current_view_uses_repair_label_instead_of_attempt_count(monkeypatch, tmp_path) -> None:
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps(
            {
                "status": "running",
                "stage": "final_repair",
                "task_id": "P4-04",
                "task_title": "data controls consent",
                "final_repair": True,
                "repair_attempt": 1,
                "final_repair_attempts": 2,
                "repair_failure_kind": "gate",
                "command_label": "codex final repair",
                "elapsed": "1m 00s",
                "pid": None,
                "run_dir": ".task-runs/example",
                "log_file": ".task-runs/example/codex-final-repair.log",
                "git_status": {"summary": "clean"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(TASK_LOOP, "CURRENT_RUN_PATH", current_path)

    rendered = TASK_LOOP.render_current_run()

    assert "repair: 1/2 (gate)" in rendered
    assert "attempt:" not in rendered


def test_negative_timeout_is_rejected() -> None:
    try:
        TASK_LOOP.normalize_timeout_seconds(-1)
    except TASK_LOOP.LoopError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative timeout should fail")


def test_negative_log_limit_is_rejected() -> None:
    try:
        TASK_LOOP.normalize_log_limit_bytes(-1)
    except TASK_LOOP.LoopError as exc:
        assert "Log limit values" in str(exc)
    else:
        raise AssertionError("negative log limit should fail")


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


def test_codex_flag_is_kept_as_noop_compatibility(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "run", "--codex"])

    args = TASK_LOOP.parse_args()

    assert args.codex is True
    assert args.max_attempts == 1
    assert args.agent_timeout_seconds == 3600
    assert args.review_timeout_seconds == 600
    assert args.repair_review_timeout_seconds == 300


def test_run_command_accepts_explicit_timeout_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_task_loop.py",
            "run",
            "--max-attempts",
            "3",
            "--agent-timeout-seconds",
            "0",
            "--review-timeout-seconds",
            "90",
        ],
    )

    args = TASK_LOOP.parse_args()

    assert args.max_attempts == 3
    assert TASK_LOOP.normalize_timeout_seconds(args.agent_timeout_seconds) is None
    assert args.review_timeout_seconds == 90


def test_run_command_accepts_manual_escape_hatches(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_task_loop.py",
            "run",
            "--allow-no-changes",
            "--codex-lean",
        ],
    )

    args = TASK_LOOP.parse_args()

    assert args.allow_no_changes is True
    assert args.codex_lean is True


def test_finish_command_defaults_to_bounded_verification(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_task_loop.py", "finish", "--task", "P3-01"])

    args = TASK_LOOP.parse_args()

    assert args.command == "finish"
    assert args.task == "P3-01"
    assert args.review_timeout_seconds == 600
    assert args.repair_review_timeout_seconds == 300
    assert args.final_repair_attempts == 2
    assert args.agent_log_limit_bytes == 0
    assert args.review_log_limit_bytes == 0
    assert args.final_repair is True
    assert args.codex_lean is False
    assert args.allow_no_changes is False
    assert args.skip_audit is False


def test_initial_prompt_includes_codex_loop_contract() -> None:
    task = {
        "id": "P1-02",
        "title": "normalization module",
        "prompt": "tasks/P1-02-normalization-module.md",
    }

    prompt = TASK_LOOP.implementation_prompt(task, 1, None)

    assert "You are executing one bounded Baseline task slice" in prompt
    assert "Pass: implementation" in prompt
    assert "Attempt:" not in prompt
    assert "The controller will run make fmt" in prompt
    assert "TASK_LOOP_DONE" in prompt


def test_repair_prompt_prioritizes_review_findings() -> None:
    task = {
        "id": "P1-02",
        "title": "normalization module",
        "prompt": "tasks/P1-02-normalization-module.md",
    }

    prompt = TASK_LOOP.implementation_prompt(
        task,
        2,
        "Review decision JSON: provenance links are wrong",
    )

    assert "Repair mode" in prompt
    assert "Do not restart broad repo discovery" in prompt
    assert "provenance links are wrong" in prompt


def test_failure_actionability_is_limited_to_gates_and_decision_json() -> None:
    assert TASK_LOOP.failure_is_actionable("make typecheck failed; see log")
    assert TASK_LOOP.failure_is_actionable("Review decision JSON:\n{}")
    assert TASK_LOOP.failure_is_actionable("Audit decision JSON:\n{}")
    assert not TASK_LOOP.failure_is_actionable("review command failed; see log")
    assert not TASK_LOOP.failure_is_actionable("audit command failed; see log")
    assert not TASK_LOOP.failure_is_actionable("codex exec failed; see log")


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


def test_repair_review_prompt_verifies_previous_findings_without_fresh_review() -> None:
    task = {
        "id": "P2-03",
        "title": "feature engine load density",
        "prompt": "tasks/P2-03-feature-engine-load-density.md",
    }

    prompt = TASK_LOOP.repair_review_prompt(
        task,
        "Review decision JSON:\n"
        '{"findings":[{"file":"apps/api/baseline_api/features/training_load.py",'
        '"line":334,"message":"EWMA skips rest days"}]}',
        status_snapshot=" M apps/api/baseline_api/features/training_load.py",
    )

    assert "repair verification, not a fresh full review" in prompt
    assert "Treat the previous actionable failure below as the checklist" in prompt
    assert "Do not search for new task-scope gaps" in prompt
    assert "possible new unrelated concerns in residual_risk" in prompt
    assert "EWMA skips rest days" in prompt
    assert " M apps/api/baseline_api/features/training_load.py" in prompt


def test_audit_prompt_adds_ui_state_machine_focus_for_ios_changes() -> None:
    task = {
        "id": "P2-05",
        "title": "ios checkin goals ui",
        "prompt": "tasks/P2-05-ios-checkin-goals-ui.md",
    }

    prompt = TASK_LOOP.audit_prompt(
        task,
        status_snapshot=" M apps/ios/Sources/BaselineApp/DailyCheckInView.swift",
    )

    assert "independent merge-readiness stance" in prompt
    assert "not a second broad code review" in prompt
    assert "Extra UI state-machine audit" in prompt
    assert "loading, empty, success, failure" in prompt
    assert "DailyCheckInView.swift" in prompt


def test_repair_audit_prompt_verifies_previous_audit_findings() -> None:
    task = {
        "id": "P3-08",
        "title": "ios briefing trace ui",
        "prompt": "tasks/P3-08-ios-briefing-trace-ui.md",
    }

    prompt = TASK_LOOP.repair_audit_prompt(
        task,
        "Audit decision JSON: error state is unreachable",
        status_snapshot=" M apps/ios/Sources/BaselineApp/DailyBriefingView.swift",
    )

    assert "repair audit, not a fresh full review" in prompt
    assert "previous actionable audit failure" in prompt
    assert "Extra UI state-machine audit" in prompt
    assert "error state is unreachable" in prompt


def test_write_prompt_snapshot_persists_generated_prompt(tmp_path) -> None:
    prompt_file = TASK_LOOP.write_prompt_snapshot(
        tmp_path,
        "audit-prompt.md",
        "Audit the task.",
    )

    assert prompt_file == tmp_path / "audit-prompt.md"
    assert prompt_file.read_text(encoding="utf-8") == "Audit the task."


def test_prompt_pack_artifacts_persist_generated_prompts(tmp_path) -> None:
    prompt_pack = TASK_LOOP.validate_prompt_pack(
        {
            "summary": "generated",
            "review_prompt": "Review this task.",
            "audit_prompt": "Audit this task.",
            "extra_audits": [
                {
                    "id": "UI State",
                    "title": "UI State Machine",
                    "reason": "Screens changed.",
                    "prompt": "Audit UI states.",
                }
            ],
            "targeted_gates": [],
            "requires_human_pause": True,
            "pause_reasons": ["Needs visual verification."],
        }
    )

    TASK_LOOP.write_prompt_pack_artifacts(tmp_path, prompt_pack)

    assert (tmp_path / "prompt-pack.json").exists()
    assert (tmp_path / "generated-review-prompt.md").read_text(encoding="utf-8") == (
        "Review this task.\n"
    )
    assert (tmp_path / "generated-audit-prompt.md").read_text(encoding="utf-8") == (
        "Audit this task.\n"
    )
    assert (tmp_path / "extra-audit-ui-state-prompt.md").read_text(encoding="utf-8") == (
        "Audit UI states.\n"
    )


def test_extra_audits_use_unique_logs_and_decision_files(monkeypatch, tmp_path) -> None:
    task = {"id": "P2-05", "title": "ios ui", "prompt": "tasks/P2-05-ios-checkin-goals-ui.md"}
    args = SimpleNamespace(
        codex_bin="codex",
        codex_lean=False,
        review_timeout_seconds=600,
        review_log_limit_bytes=1_000_000,
        heartbeat_seconds=0,
    )
    prompt_pack = TASK_LOOP.validate_prompt_pack(
        {
            "summary": "generated",
            "review_prompt": "review",
            "audit_prompt": "audit",
            "extra_audits": [
                {
                    "id": "ui-state-machine",
                    "title": "UI state machine",
                    "reason": "View changed.",
                    "prompt": "Audit UI states.",
                }
            ],
            "targeted_gates": [],
            "requires_human_pause": False,
            "pause_reasons": [],
        }
    )
    calls: list[dict[str, object]] = []

    def fake_run_audit(*args: object, **kwargs: object) -> tuple[bool, str]:
        calls.append(kwargs)
        return True, "extra audit passed"

    monkeypatch.setattr(TASK_LOOP, "run_audit", fake_run_audit)

    ok, result = TASK_LOOP.run_extra_audits(task, tmp_path, args, prompt_pack, {})

    assert ok is True
    assert result == "extra audits passed"
    assert calls[0]["prompt_name"] == "extra-audit-ui-state-machine-prompt.md"
    assert calls[0]["output_name"] == "extra-audit-ui-state-machine-decision.json"
    assert calls[0]["log_name"] == "extra-audit-ui-state-machine.log"


def test_pause_reasons_include_ui_and_generated_pack_requests() -> None:
    task = {
        "id": "P2-05",
        "title": "ios checkin goals ui",
        "prompt": "tasks/P2-05-ios-checkin-goals-ui.md",
    }
    prompt_pack = TASK_LOOP.validate_prompt_pack(
        {
            "summary": "generated",
            "review_prompt": "review",
            "audit_prompt": "audit",
            "extra_audits": [],
            "targeted_gates": [],
            "requires_human_pause": True,
            "pause_reasons": ["Generated pack wants a product check."],
        }
    )

    reasons = TASK_LOOP.pause_reasons_for_task(
        task,
        prompt_pack,
        " M apps/ios/Sources/BaselineApp/DailyCheckInView.swift",
    )

    assert "Generated pack wants a product check." in reasons
    assert "UI/state-machine changes need human visual verification before continuing." in reasons


def test_codex_implementation_command_uses_lean_exec_by_default() -> None:
    args = SimpleNamespace(agent="codex", codex_bin="codex", codex_lean=True)

    label, command = TASK_LOOP.implementation_agent_command(args)

    assert label == "implementation (lean)"
    assert command[:2] == ["codex", "exec"]
    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert "--color" in command
    assert "--sandbox" in command
    assert command[-1] == "-"


def test_codex_implementation_command_can_load_full_config() -> None:
    args = SimpleNamespace(agent="codex", codex_bin="codex", codex_lean=False)

    label, command = TASK_LOOP.implementation_agent_command(args)

    assert label == "implementation"
    assert "--ignore-user-config" not in command
    assert "--ephemeral" not in command


def test_codex_invocation_keeps_prompt_on_stdin() -> None:
    args = SimpleNamespace(agent="codex", codex_bin="codex", codex_lean=True)

    label, command, input_text, logged_command = TASK_LOOP.implementation_agent_invocation(
        args,
        "do the task",
    )

    assert label == "implementation (lean)"
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
            "max_attempts": 1,
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


def test_run_logged_stops_after_success_sentinel(tmp_path) -> None:
    log_file = tmp_path / "sentinel.log"
    status_file = tmp_path / "current.json"

    code = TASK_LOOP.run_logged(
        [
            sys.executable,
            "-c",
            "import time; print('TASK_LOOP_DONE', flush=True); time.sleep(5)",
        ],
        log_file,
        timeout_seconds=30,
        status_file=status_file,
        status={"task_id": "P1-02", "stage": "implementation"},
        heartbeat_seconds=0,
        success_sentinel="TASK_LOOP_DONE",
    )

    assert code == 0
    log = log_file.read_text(encoding="utf-8")
    assert "[success_sentinel] TASK_LOOP_DONE" in log
    state = json.loads(status_file.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["exit_code"] == 0


def test_run_logged_ignores_sentinel_mentions_that_are_not_exact_lines(tmp_path) -> None:
    log_file = tmp_path / "quoted-sentinel.log"
    status_file = tmp_path / "current.json"

    code = TASK_LOOP.run_logged(
        [
            sys.executable,
            "-c",
            "print('Mention `TASK_LOOP_DONE` in prompt text', flush=True)",
        ],
        log_file,
        timeout_seconds=30,
        status_file=status_file,
        status={"task_id": "P1-02", "stage": "implementation"},
        heartbeat_seconds=0,
        success_sentinel="TASK_LOOP_DONE",
    )

    assert code == 0
    log = log_file.read_text(encoding="utf-8")
    assert "[success_sentinel]" not in log
    state = json.loads(status_file.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["exit_code"] == 0


def test_run_logged_writes_run_summary_with_token_usage(tmp_path) -> None:
    log_file = tmp_path / "tokenized.log"
    status_file = tmp_path / "current.json"

    code = TASK_LOOP.run_logged(
        [
            sys.executable,
            "-c",
            "print('tokens used'); print('1,234')",
        ],
        log_file,
        timeout_seconds=30,
        status_file=status_file,
        status={"task_id": "P1-02", "stage": "implementation"},
        heartbeat_seconds=0,
    )

    assert code == 0
    summary = json.loads((tmp_path / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["stages"][-1]["stage"] == "implementation"
    assert summary["stages"][-1]["tokens_used"] == 1234
    assert summary["stages"][-1]["log_bytes"] > 0


def test_run_logged_stops_at_log_limit(tmp_path) -> None:
    log_file = tmp_path / "chatty.log"
    status_file = tmp_path / "current.json"

    code = TASK_LOOP.run_logged(
        [sys.executable, "-c", "print('x' * 5000, flush=True); import time; time.sleep(5)"],
        log_file,
        timeout_seconds=30,
        status_file=status_file,
        status={"task_id": "P1-02", "stage": "implementation"},
        heartbeat_seconds=0,
        max_log_bytes=1000,
    )

    assert code == TASK_LOOP.LOG_LIMIT_EXIT_CODE
    log = log_file.read_text(encoding="utf-8")
    assert "[max_log_bytes] 1000" in log
    state = json.loads(status_file.read_text(encoding="utf-8"))
    assert state["status"] == "log_limited"
    assert state["exit_code"] == TASK_LOOP.LOG_LIMIT_EXIT_CODE


def test_protected_path_violations_block_non_automation_tasks() -> None:
    task = {
        "id": "P4-01",
        "title": "memory daily weekly",
        "prompt": "tasks/P4-01-memory-daily-weekly.md",
    }

    violations = TASK_LOOP.protected_path_violations(
        task,
        [
            "scripts/run_task_loop.py",
            "docs/automation/loop-engineering.md",
            "tasks/ledger.json",
        ],
    )

    assert violations == ["docs/automation/loop-engineering.md", "scripts/run_task_loop.py"]


def test_protected_path_violations_allow_automation_tasks() -> None:
    task = {
        "id": "AUTO-01",
        "title": "automation controller hardening",
        "prompt": "tasks/P4-01-memory-daily-weekly.md",
    }

    assert TASK_LOOP.protected_path_violations(task, ["scripts/run_task_loop.py"]) == []


def test_restore_tracked_protected_churn_for_non_automation_tasks(monkeypatch) -> None:
    task = {
        "id": "P4-03",
        "title": "feedback outcome loop",
        "prompt": "tasks/P4-03-feedback-outcome-loop.md",
    }
    commands: list[list[str]] = []

    monkeypatch.setattr(
        TASK_LOOP,
        "git_status_entries",
        lambda: [
            (" M", "scripts/run_task_loop.py"),
            (" M", "apps/api/baseline_api/schemas/api.py"),
        ],
    )
    monkeypatch.setattr(
        TASK_LOOP.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    restored = TASK_LOOP.restore_tracked_protected_churn(task)

    assert restored == ["scripts/run_task_loop.py"]
    assert commands == [["git", "restore", "--", "scripts/run_task_loop.py"]]


def test_restore_protected_churn_blocks_new_controller_files(monkeypatch) -> None:
    task = {
        "id": "P4-03",
        "title": "feedback outcome loop",
        "prompt": "tasks/P4-03-feedback-outcome-loop.md",
    }

    monkeypatch.setattr(
        TASK_LOOP,
        "git_status_entries",
        lambda: [("??", "docs/automation/new-controller-note.md")],
    )

    try:
        TASK_LOOP.restore_tracked_protected_churn(task)
    except TASK_LOOP.LoopError as exc:
        assert "new, copied, or renamed task-loop controller files" in str(exc)
    else:
        raise AssertionError("new protected files should still block")


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

    audit_context = TASK_LOOP.format_audit_failure(output_file, decision)

    assert "Audit decision JSON" in audit_context
    assert "Replay missed normalization repair" in audit_context


def test_review_prompts_discourage_broad_tree_listing() -> None:
    task = {
        "id": "P3-02",
        "title": "reasoning engine",
        "prompt": "tasks/P3-02-reasoning-engine.md",
    }

    review_prompt = TASK_LOOP.review_prompt(task, "?? apps/api/tests/test_reasoning_engine.py")
    repair_prompt = TASK_LOOP.repair_review_prompt(
        task,
        "review failed; see file\n\nReview decision JSON:\n{}",
        "?? apps/api/tests/test_reasoning_engine.py",
    )
    audit_prompt = TASK_LOOP.audit_prompt(task, "?? apps/api/tests/test_reasoning_engine.py")
    repair_audit_prompt = TASK_LOOP.repair_audit_prompt(
        task,
        "audit failed; see file\n\nAudit decision JSON:\n{}",
        "?? apps/api/tests/test_reasoning_engine.py",
    )

    assert "Do not enumerate broad directories or test trees" in review_prompt
    assert "Do not enumerate broad directories or test trees" in repair_prompt
    assert "Do not enumerate broad directories or test trees" in audit_prompt
    assert "Do not enumerate broad directories or test trees" in repair_audit_prompt
    assert "find apps/api/tests" in review_prompt
    assert "find apps/api/tests" in repair_prompt
    assert "find apps/api/tests" in audit_prompt
    assert "find apps/api/tests" in repair_audit_prompt


def test_final_repair_accepts_only_actionable_gate_or_decision_failures() -> None:
    review_actionable = "review failed; see file\n\nReview decision JSON:\n{}"
    audit_actionable = "audit failed; see file\n\nAudit decision JSON:\n{}"
    non_actionable = "review command failed; see file\n\nLog tail:\nturn interrupted"

    assert TASK_LOOP.failure_is_actionable(review_actionable) is True
    assert TASK_LOOP.failure_is_actionable(audit_actionable) is True
    assert TASK_LOOP.failure_is_actionable("make test failed; see file") is True
    assert TASK_LOOP.failure_is_actionable(non_actionable) is False


def finish_args(**overrides: object) -> SimpleNamespace:
    values = {
        "allow_no_changes": False,
        "prior_verification_file": None,
        "heartbeat_seconds": 0,
        "skip_review": False,
        "skip_audit": False,
        "codex_bin": "codex",
        "codex_lean": False,
        "review_timeout_seconds": 600,
        "review_log_limit_bytes": 0,
        "skip_prompt_pack": False,
        "pause_policy": "auto",
        "final_repair": True,
        "final_repair_attempts": 2,
        "agent_log_limit_bytes": 0,
        "final_repair_timeout_seconds": 900,
        "repair_review_timeout_seconds": 300,
        "commit": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_prior_verification_evidence_matches_only_proven_gates(tmp_path) -> None:
    evidence_file = tmp_path / "verification.txt"
    evidence_file.write_text(
        "\n".join(
            [
                "Verification:",
                "make lint passed.",
                "make typecheck passed.",
                "make test passed: 210 passed, 86 skipped, 1 warning.",
                "DB-backed tests were skipped because local Postgres was unavailable.",
            ]
        ),
        encoding="utf-8",
    )
    ledger = {
        "quality_gates": [
            "make fmt",
            "make lint",
            "make typecheck",
            "make test",
        ]
    }

    verified, path = TASK_LOOP.prior_verified_gates(ledger, str(evidence_file))

    assert path == evidence_file
    assert verified == {"make lint", "make typecheck", "make test"}


def test_quality_gates_reuse_prior_evidence_for_matching_gates(
    monkeypatch,
    tmp_path,
) -> None:
    ledger = {"quality_gates": ["make fmt", "make lint", "make typecheck", "make test"]}
    evidence_file = tmp_path / "verification.txt"
    evidence_file.write_text("make lint passed\nmake typecheck passed\nmake test passed\n")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        TASK_LOOP,
        "run_logged",
        lambda command, *args, **kwargs: calls.append(command) or 0,
    )

    ok, result = TASK_LOOP.run_quality_gates(
        tmp_path / "run",
        ledger,
        {},
        0,
        verified_gates={"make lint", "make typecheck", "make test"},
        prior_verification_path=evidence_file,
    )

    assert ok is True
    assert result == "quality gates passed"
    assert calls == [["make", "fmt"]]
    assert (
        (tmp_path / "run" / "02-gate-make-lint.log")
        .read_text(encoding="utf-8")
        .startswith("$ prior verification evidence")
    )


def test_finish_task_requires_existing_diff_by_default(monkeypatch) -> None:
    monkeypatch.setattr(TASK_LOOP, "git_status_lines", lambda: [])
    task = {"id": "P3-01", "title": "goal management"}

    try:
        TASK_LOOP.run_finish_task({"quality_gates": []}, task, finish_args())
    except TASK_LOOP.LoopError as exc:
        assert "No existing diff" in str(exc)
    else:
        raise AssertionError("finish should require an existing diff by default")


def test_finish_task_runs_gates_review_audit_and_complete_without_agent(monkeypatch) -> None:
    task = {"id": "P3-01", "title": "goal management"}
    ledger = {"quality_gates": ["make test"]}
    calls: list[str] = []

    monkeypatch.setattr(TASK_LOOP, "git_status_lines", lambda: [" M apps/api/example.py"])
    monkeypatch.setattr(
        TASK_LOOP,
        "write_static_run_state",
        lambda *args, **kwargs: calls.append(f"state:{kwargs['stage']}"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_quality_gates",
        lambda *args, **kwargs: calls.append("gates") or (True, "quality gates passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "cleanup_generated_python_artifacts",
        lambda: calls.append("cleanup") and 0,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_review",
        lambda *args, **kwargs: calls.append("review") or (True, "review passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_audit",
        lambda *args, **kwargs: calls.append("audit") or (True, "audit passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "prepare_prompt_pack",
        lambda *args, **kwargs: (
            calls.append("prompt-pack")
            or {
                "review_prompt": "review",
                "audit_prompt": "audit",
                "extra_audits": [],
                "requires_human_pause": False,
                "pause_reasons": [],
                "_scope_files": ["apps/api/example.py"],
            }
        ),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_extra_audits",
        lambda *args, **kwargs: calls.append("extra-audits") or (True, "extra audits passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "remember_pause_reasons",
        lambda *args, **kwargs: calls.append("pause") or [],
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "complete_task",
        lambda *args, **kwargs: calls.append("complete"),
    )

    assert TASK_LOOP.run_finish_task(ledger, task, finish_args()) is True

    assert calls == [
        "state:finish_existing_diff",
        "gates",
        "cleanup",
        "prompt-pack",
        "review",
        "audit",
        "extra-audits",
        "complete",
        "pause",
        "state:complete",
    ]


def test_final_repair_allows_finish_args_without_max_attempts(monkeypatch) -> None:
    task = {
        "id": "P3-01",
        "title": "goal management",
        "prompt": "tasks/P3-01-goal-management.md",
    }
    calls: list[str] = []

    monkeypatch.setattr(
        TASK_LOOP,
        "implementation_prompt",
        lambda _task, attempt, _failure, *_args: calls.append(f"attempt:{attempt}") or "fix",
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_logged",
        lambda *args, **kwargs: calls.append("codex") or 0,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_quality_gates",
        lambda *args, **kwargs: calls.append("gates") or (True, "quality gates passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "cleanup_generated_python_artifacts",
        lambda: calls.append("cleanup") and 0,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_review",
        lambda *args, **kwargs: calls.append("review") or (True, "review passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_audit",
        lambda *args, **kwargs: calls.append("audit") or (True, "audit passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "prepare_prompt_pack",
        lambda *args, **kwargs: (
            calls.append("prompt-pack")
            or {
                "review_prompt": "review",
                "audit_prompt": "audit",
                "extra_audits": [],
                "requires_human_pause": False,
                "pause_reasons": [],
            }
        ),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_extra_audits",
        lambda *args, **kwargs: calls.append("extra-audits") or (True, "extra audits passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "remember_pause_reasons",
        lambda *args, **kwargs: calls.append("pause") or [],
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "complete_task",
        lambda *args, **kwargs: calls.append("complete"),
    )
    monkeypatch.setattr(TASK_LOOP, "write_static_run_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(TASK_LOOP, "review_scope_snapshot", lambda: " M apps/api/example.py")

    assert (
        TASK_LOOP.run_final_repair(
            {"quality_gates": []},
            task,
            finish_args(),
            "review failed; see file\n\nReview decision JSON:\n{}",
        )
        is True
    )

    assert calls == [
        "attempt:1",
        "codex",
        "gates",
        "cleanup",
        "prompt-pack",
        "review",
        "audit",
        "extra-audits",
        "complete",
        "pause",
    ]


def test_final_repair_retries_actionable_audit_findings(monkeypatch) -> None:
    task = {
        "id": "P3-08",
        "title": "ios briefing trace ui",
        "prompt": "tasks/P3-08-ios-briefing-trace-ui.md",
    }
    calls: list[str] = []
    audit_results = [
        (False, "audit failed; see file\n\nAudit decision JSON:\n{}"),
        (True, "audit passed"),
    ]

    monkeypatch.setattr(
        TASK_LOOP,
        "implementation_prompt",
        lambda _task, attempt, _failure, *_args: calls.append(f"attempt:{attempt}") or "fix",
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_logged",
        lambda *args, **kwargs: calls.append("codex") or 0,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_quality_gates",
        lambda *args, **kwargs: calls.append("gates") or (True, "quality gates passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "cleanup_generated_python_artifacts",
        lambda: calls.append("cleanup") and 0,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_audit",
        lambda *args, **kwargs: calls.append("audit") or audit_results.pop(0),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "prepare_prompt_pack",
        lambda *args, **kwargs: (
            calls.append("prompt-pack")
            or {
                "review_prompt": "review",
                "audit_prompt": "audit",
                "extra_audits": [],
                "requires_human_pause": False,
                "pause_reasons": [],
            }
        ),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_extra_audits",
        lambda *args, **kwargs: calls.append("extra-audits") or (True, "extra audits passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "remember_pause_reasons",
        lambda *args, **kwargs: calls.append("pause") or [],
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "complete_task",
        lambda *args, **kwargs: calls.append("complete"),
    )
    monkeypatch.setattr(TASK_LOOP, "write_static_run_state", lambda *args, **kwargs: None)

    assert (
        TASK_LOOP.run_final_repair(
            {"quality_gates": []},
            task,
            finish_args(),
            "audit failed; see file\n\nAudit decision JSON:\n{}",
        )
        is True
    )

    assert calls == [
        "attempt:1",
        "codex",
        "gates",
        "cleanup",
        "audit",
        "attempt:2",
        "codex",
        "gates",
        "cleanup",
        "audit",
        "complete",
    ]


def test_final_repair_keeps_decision_budget_after_gate_repairs(monkeypatch) -> None:
    task = {
        "id": "P4-01",
        "title": "memory daily weekly",
        "prompt": "tasks/P4-01-memory-daily-weekly.md",
    }
    calls: list[str] = []
    gate_results = [
        (False, "make typecheck failed; see file"),
        (True, "quality gates passed"),
        (True, "quality gates passed"),
    ]
    review_results = [
        (False, "review failed; see file\n\nReview decision JSON:\n{}"),
        (True, "review passed"),
    ]

    monkeypatch.setattr(
        TASK_LOOP,
        "implementation_prompt",
        lambda _task, attempt, _failure, *_args: calls.append(f"attempt:{attempt}") or "fix",
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_logged",
        lambda *args, **kwargs: calls.append("codex") or 0,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_quality_gates",
        lambda *args, **kwargs: calls.append("gates") or gate_results.pop(0),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "cleanup_generated_python_artifacts",
        lambda: calls.append("cleanup") and 0,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_review",
        lambda *args, **kwargs: calls.append("review") or review_results.pop(0),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_audit",
        lambda *args, **kwargs: calls.append("audit") or (True, "audit passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "prepare_prompt_pack",
        lambda *args, **kwargs: (
            calls.append("prompt-pack")
            or {
                "review_prompt": "review",
                "audit_prompt": "audit",
                "extra_audits": [],
                "requires_human_pause": False,
                "pause_reasons": [],
                "_scope_files": ["apps/api/example.py"],
            }
        ),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_extra_audits",
        lambda *args, **kwargs: calls.append("extra-audits") or (True, "extra audits passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "remember_pause_reasons",
        lambda *args, **kwargs: calls.append("pause") or [],
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "complete_task",
        lambda *args, **kwargs: calls.append("complete"),
    )
    monkeypatch.setattr(TASK_LOOP, "write_static_run_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(TASK_LOOP, "review_scope_snapshot", lambda: " M apps/api/example.py")

    assert (
        TASK_LOOP.run_final_repair(
            {"quality_gates": []},
            task,
            finish_args(),
            "make typecheck failed; see file",
        )
        is True
    )

    assert calls == [
        "attempt:1",
        "codex",
        "gates",
        "attempt:2",
        "codex",
        "gates",
        "cleanup",
        "prompt-pack",
        "review",
        "attempt:3",
        "codex",
        "gates",
        "cleanup",
        "review",
        "audit",
        "extra-audits",
        "complete",
        "pause",
    ]


def test_final_repair_reuses_prompt_pack_when_scope_is_unchanged(monkeypatch) -> None:
    task = {
        "id": "P4-02",
        "title": "memory monthly quarterly",
        "prompt": "tasks/P4-02-memory-monthly-quarterly.md",
    }
    prompt_pack = {
        "review_prompt": "review",
        "audit_prompt": "audit",
        "extra_audits": [],
        "requires_human_pause": False,
        "pause_reasons": [],
        "_scope_files": ["apps/api/baseline_api/memory/compiler.py"],
    }
    calls: list[str] = []

    monkeypatch.setattr(
        TASK_LOOP,
        "implementation_prompt",
        lambda _task, attempt, _failure, *_args: calls.append(f"attempt:{attempt}") or "fix",
    )
    monkeypatch.setattr(TASK_LOOP, "run_logged", lambda *args, **kwargs: calls.append("codex") or 0)
    monkeypatch.setattr(
        TASK_LOOP,
        "run_quality_gates",
        lambda *args, **kwargs: calls.append("gates") or (True, "quality gates passed"),
    )
    monkeypatch.setattr(TASK_LOOP, "cleanup_generated_python_artifacts", lambda: 0)
    monkeypatch.setattr(
        TASK_LOOP,
        "review_scope_snapshot",
        lambda: " M apps/api/baseline_api/memory/compiler.py",
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "write_prompt_pack_artifacts",
        lambda *args, **kwargs: calls.append("reuse-pack"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "prepare_prompt_pack",
        lambda *args, **kwargs: calls.append("unexpected-generate") or prompt_pack,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_review",
        lambda *args, **kwargs: calls.append("review") or (True, "review passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_audit",
        lambda *args, **kwargs: calls.append("audit") or (True, "audit passed"),
    )
    monkeypatch.setattr(TASK_LOOP, "remember_pause_reasons", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        TASK_LOOP,
        "complete_task",
        lambda *args, **kwargs: calls.append("complete"),
    )
    monkeypatch.setattr(TASK_LOOP, "write_static_run_state", lambda *args, **kwargs: None)

    assert (
        TASK_LOOP.run_final_repair(
            {"quality_gates": []},
            task,
            finish_args(),
            "review failed; see file\n\nReview decision JSON:\n{}",
            prompt_pack,
        )
        is True
    )

    assert "unexpected-generate" not in calls
    assert calls == [
        "attempt:1",
        "codex",
        "gates",
        "reuse-pack",
        "review",
        "audit",
        "complete",
    ]


def test_final_repair_regenerates_prompt_pack_when_scope_expands(monkeypatch) -> None:
    task = {
        "id": "P4-02",
        "title": "memory monthly quarterly",
        "prompt": "tasks/P4-02-memory-monthly-quarterly.md",
    }
    old_pack = {
        "review_prompt": "old review",
        "audit_prompt": "old audit",
        "extra_audits": [],
        "requires_human_pause": False,
        "pause_reasons": [],
        "_scope_files": ["apps/api/baseline_api/memory/compiler.py"],
    }
    new_pack = {
        "review_prompt": "new review",
        "audit_prompt": "new audit",
        "extra_audits": [],
        "requires_human_pause": False,
        "pause_reasons": [],
        "_scope_files": [
            "apps/api/baseline_api/memory/compiler.py",
            "apps/api/baseline_api/memory/service.py",
        ],
    }
    calls: list[str] = []

    monkeypatch.setattr(
        TASK_LOOP,
        "implementation_prompt",
        lambda _task, attempt, _failure, *_args: calls.append(f"attempt:{attempt}") or "fix",
    )
    monkeypatch.setattr(TASK_LOOP, "run_logged", lambda *args, **kwargs: calls.append("codex") or 0)
    monkeypatch.setattr(
        TASK_LOOP,
        "run_quality_gates",
        lambda *args, **kwargs: calls.append("gates") or (True, "quality gates passed"),
    )
    monkeypatch.setattr(TASK_LOOP, "cleanup_generated_python_artifacts", lambda: 0)
    monkeypatch.setattr(
        TASK_LOOP,
        "review_scope_snapshot",
        lambda: (
            " M apps/api/baseline_api/memory/compiler.py\n"
            " M apps/api/baseline_api/memory/service.py"
        ),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "prepare_prompt_pack",
        lambda *args, **kwargs: calls.append("generate-pack") or new_pack,
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_review",
        lambda *args, **kwargs: calls.append("review") or (True, "review passed"),
    )
    monkeypatch.setattr(
        TASK_LOOP,
        "run_audit",
        lambda *args, **kwargs: calls.append("audit") or (True, "audit passed"),
    )
    monkeypatch.setattr(TASK_LOOP, "remember_pause_reasons", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        TASK_LOOP,
        "complete_task",
        lambda *args, **kwargs: calls.append("complete"),
    )
    monkeypatch.setattr(TASK_LOOP, "write_static_run_state", lambda *args, **kwargs: None)

    assert (
        TASK_LOOP.run_final_repair(
            {"quality_gates": []},
            task,
            finish_args(),
            "review failed; see file\n\nReview decision JSON:\n{}",
            old_pack,
        )
        is True
    )

    assert calls == [
        "attempt:1",
        "codex",
        "gates",
        "generate-pack",
        "review",
        "audit",
        "complete",
    ]


def test_implementation_timeout_candidate_changes_are_detected() -> None:
    before: list[str] = []
    after = [" M packages/eval/scorers.py", "?? apps/api/tests/features/"]

    assert TASK_LOOP.implementation_has_candidate_changes(before, after) is True


def test_implementation_timeout_without_new_changes_is_not_candidate() -> None:
    before = [" M existing.py"]

    assert TASK_LOOP.implementation_has_candidate_changes(before, before) is False
    assert TASK_LOOP.implementation_has_candidate_changes(before, None) is False
    assert TASK_LOOP.implementation_has_candidate_changes([], []) is False
