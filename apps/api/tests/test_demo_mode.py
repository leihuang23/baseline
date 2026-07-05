"""Portfolio demo mode and private-data leak tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import packages.eval.demo as demo_module
import pytest
from packages.eval.definitions import EvalContext, EvalType
from packages.eval.demo import (
    DEMO_SCENARIOS,
    build_demo_artifacts,
    demo_artifacts_have_no_private_leaks,
    demo_privacy_suites,
    scan_for_private_data_leaks,
    scan_mapping_for_private_data_leaks,
    write_demo_artifacts,
)
from packages.eval.runner import GATED_FAILURE_TYPES
from packages.eval.suites import build_default_registry
from packages.fixtures import get_scenario

from baseline_api.briefing import DailyBriefingService
from baseline_api.memory.service import MemoryService

FORBIDDEN_PATTERNS = (
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"alice",
        r"bob",
        r"@\w+",
        r"api[_-]?key",
        r"diagnosed",
        r"doctor",
        r"free[-_\s]?text",
        r"medication",
        r"patient",
        r"phone",
        r"secret(?:\s+(?:token|key|value|credential|password)|\s*[:=])",
        r"sexual",
        r"source[_\s-]?sample",
        r"raw sample",
        r"sk-[a-z0-9]",
        r"BEGIN [A-Z ]*PRIVATE KEY",
    )
)


def test_demo_scenarios_cover_reviewable_cases() -> None:
    """Demo mode exposes at least five pre-baked review scenarios."""

    names = [scenario.name for scenario in DEMO_SCENARIOS]

    assert len(names) >= 5
    assert "demo_60_day_persona" in names
    assert len(names) == len(set(names))
    assert {scenario.fixture_name for scenario in DEMO_SCENARIOS} == {"demo_60_day_persona"}


def test_demo_leak_suite_is_registered_for_ci() -> None:
    """The package eval registry includes a gated privacy check for demo artifacts."""

    registry = build_default_registry()

    suite_names = {
        f"demo_mode_private_data_leak_check__{scenario.name}" for scenario in DEMO_SCENARIOS
    }
    assert suite_names <= set(registry.names())
    assert EvalType.PRIVACY in GATED_FAILURE_TYPES
    assert {suite.scenario_name for suite in demo_privacy_suites()} == {
        scenario.name for scenario in DEMO_SCENARIOS
    }
    assert {suite.input_fixture for suite in demo_privacy_suites()} == {"demo_60_day_persona"}


def test_demo_artifacts_exercise_full_offline_loop() -> None:
    """A demo build produces ingestion, features, reasoning, briefing, memory, and dashboard."""

    artifacts = build_demo_artifacts()

    assert artifacts["mode"] == "demo"
    assert artifacts["requires_production_secrets"] is False
    assert artifacts["external_calls_enabled"] is False
    assert artifacts["ingestion"]["client_sync_id"].startswith("synthetic:demo_60_day_persona")
    assert artifacts["pipeline"]["entrypoint"] == "DailyBriefingService.generate_daily"
    assert [stage["name"] for stage in artifacts["pipeline"]["stages"]] == [
        "enqueue",
        "job_running",
        "features",
        "data_freshness",
        "retrieval",
        "reasoning",
        "llm_explanation",
        "safety",
        "memory",
        "persistence",
    ]
    persisted = artifacts["pipeline"]["persisted_records"]
    assert persisted["raw_health_samples"] > 0
    assert persisted["normalized_health_metrics"] > 0
    assert persisted["sleep_sessions"] > 0
    assert persisted["workout_sessions"] > 0
    assert persisted["daily_check_ins"] > 0
    assert persisted["derived_daily_features"] == 1
    assert {
        key: persisted[key]
        for key in (
            "reasoning_traces",
            "readiness_assessments",
            "recommendations",
            "memory_summaries",
            "model_runs",
        )
    } == {
        "reasoning_traces": 1,
        "readiness_assessments": 1,
        "recommendations": 1,
        "memory_summaries": 2,
        "model_runs": 1,
    }
    assert artifacts["features"]["feature_version"]
    assert artifacts["reasoning"]["reasoning_trace_id"]
    assert artifacts["briefing"]["model_provider"] == "recorded_mock"
    assert artifacts["briefing"]["summary"]
    assert artifacts["memory"]["summaries"]
    assert artifacts["dashboard"]["demoScenarios"]
    assert artifacts["export"]["privacy_mode"] == "demo_synthetic_only"


def test_demo_loop_rejects_degraded_feature_stage(monkeypatch) -> None:
    """Demo mode must not pass when product feature loading falls back to degradation."""

    def fail_feature_load(self: DailyBriefingService, **kwargs: Any) -> Any:
        _ = (self, kwargs)
        raise RuntimeError("forced feature failure")

    monkeypatch.setattr(DailyBriefingService, "_load_or_compute_features", fail_feature_load)

    with pytest.raises(RuntimeError, match="features status='degraded'"):
        build_demo_artifacts()


def test_demo_eval_fails_when_fixture_persistence_is_bypassed(monkeypatch) -> None:
    """The CI privacy eval also proves synthetic ingestion/check-ins were persisted."""

    def bypass_fixture_load(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        return None

    monkeypatch.setattr(demo_module, "load_fixture", bypass_fixture_load)
    suite = demo_privacy_suites()[0]
    context = EvalContext(
        suite_name=suite.name,
        eval_type=suite.eval_type,
        scenario_name=suite.scenario_name,
        fixture=get_scenario(suite.input_fixture),
        expected_properties=suite.expected_properties,
        mocked_model_response=suite.mocked_model_response,
    )

    score = demo_artifacts_have_no_private_leaks(context)

    assert score.passed is False
    assert "raw_health_samples=0" in score.observed["artifact_build_error"]
    assert "daily_check_ins=0" in score.observed["artifact_build_error"]


def test_demo_loop_uses_product_loaders_and_memory_service(monkeypatch) -> None:
    """The offline demo still runs through product feature/check-in/memory integrations."""

    calls = {
        "features": 0,
        "checkin": 0,
        "daily_memory": 0,
        "weekly_memory": 0,
    }
    original_features = DailyBriefingService._load_or_compute_features_with_degraded_mode
    original_checkin = DailyBriefingService._load_checkin
    original_daily = MemoryService.generate_daily_summary
    original_weekly = MemoryService.generate_weekly_summary

    def wrapped_features(self: DailyBriefingService, **kwargs: Any) -> Any:
        calls["features"] += 1
        return original_features(self, **kwargs)

    def wrapped_checkin(self: DailyBriefingService, *args: Any, **kwargs: Any) -> Any:
        calls["checkin"] += 1
        return original_checkin(self, *args, **kwargs)

    def wrapped_daily(self: MemoryService, **kwargs: Any) -> Any:
        calls["daily_memory"] += 1
        return original_daily(self, **kwargs)

    def wrapped_weekly(self: MemoryService, **kwargs: Any) -> Any:
        calls["weekly_memory"] += 1
        return original_weekly(self, **kwargs)

    monkeypatch.setattr(
        DailyBriefingService,
        "_load_or_compute_features_with_degraded_mode",
        wrapped_features,
    )
    monkeypatch.setattr(DailyBriefingService, "_load_checkin", wrapped_checkin)
    monkeypatch.setattr(MemoryService, "generate_daily_summary", wrapped_daily)
    monkeypatch.setattr(MemoryService, "generate_weekly_summary", wrapped_weekly)

    artifacts = build_demo_artifacts()

    assert calls == {
        "features": 1,
        "checkin": 2,
        "daily_memory": 1,
        "weekly_memory": 1,
    }
    assert artifacts["pipeline"]["persisted_records"]["memory_summaries"] == 2


def test_demo_artifacts_are_deterministic_for_every_scenario(tmp_path: Path) -> None:
    """The same seed and scenario produce byte-identical artifacts across runs."""

    for scenario in DEMO_SCENARIOS:
        first_paths = write_demo_artifacts(tmp_path / "first" / scenario.name, scenario.name)
        second_paths = write_demo_artifacts(tmp_path / "second" / scenario.name, scenario.name)

        first_manifest = json.loads(first_paths["manifest"].read_text(encoding="utf-8"))
        second_manifest = json.loads(second_paths["manifest"].read_text(encoding="utf-8"))

        assert first_manifest == second_manifest
        for artifact_name in ("briefing", "trace", "memory", "dashboard", "export"):
            assert (
                first_paths[artifact_name].read_bytes() == second_paths[artifact_name].read_bytes()
            )


def test_demo_private_data_leak_suite_covers_artifacts_dashboard_and_export(tmp_path: Path) -> None:
    """Demo artifacts, dashboard data, and export payloads contain no private markers."""

    paths = write_demo_artifacts(tmp_path)
    rendered_text = "\n".join(path.read_text(encoding="utf-8") for path in paths.values())

    leak_report = scan_for_private_data_leaks(paths.values())

    assert leak_report == {"passed": True, "findings": []}
    for pattern in FORBIDDEN_PATTERNS:
        assert not pattern.search(rendered_text), pattern.pattern


def test_demo_leak_scanner_catches_realistic_private_markers() -> None:
    """The scanner catches realistic prose and integration-source leak markers."""

    payload = {
        "dashboard": "Synthetic panel accidentally included doctor follow-up.",
        "export": "HealthKit source payload carried a secret token.",
    }

    leak_report = scan_mapping_for_private_data_leaks(payload)

    assert leak_report["passed"] is False
    matched = {finding["matched"] for finding in leak_report["findings"]}
    assert "doctor" in matched
    assert "HealthKit" in matched
    assert "secret token" in matched


def test_demo_leak_scanner_rejects_prompt_payload_fields() -> None:
    """Prompt/message payloads are forbidden even when their text looks sanitized."""

    payload = {
        "briefing": {
            "summary": "Synthetic readiness briefing.",
            "prompt_payload": {
                "system_prompt": "You are concise.",
                "messages": [{"role": "user", "content": "Summarize the day."}],
            },
        }
    }

    leak_report = scan_mapping_for_private_data_leaks(payload)

    assert leak_report["passed"] is False
    matched = {finding["matched"] for finding in leak_report["findings"]}
    assert {"prompt_payload", "system_prompt", "messages"} <= matched


def test_every_demo_scenario_can_build_without_live_services() -> None:
    """Each selectable demo scenario can produce the offline walkthrough artifacts."""

    for scenario in DEMO_SCENARIOS:
        artifacts = build_demo_artifacts(scenario.name)

        assert artifacts["scenario"]["name"] == scenario.name
        assert artifacts["scenario"]["fixture_name"] == "demo_60_day_persona"
        assert artifacts["scenario"]["days"] == 60
        assert artifacts["ingestion"]["client_sync_id"].startswith("synthetic:demo_60_day_persona")
        assert artifacts["requires_production_secrets"] is False
        assert artifacts["external_calls_enabled"] is False
        assert artifacts["leak_report"] == {"passed": True, "findings": []}
        assert artifacts["dashboard"]["recommendationTraces"]
        assert artifacts["export"]["records"]
        assert artifacts["briefing"]["safety_status"] in {"passed", "blocked"}
