"""Deterministic portfolio demo mode and leak checks."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from baseline_api.briefing import DailyBriefingService
from baseline_api.db.models.assessment import (
    DailyAnalysisJob,
    ReadinessAssessment,
    ReasoningTrace,
    Recommendation,
)
from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.enums import RunType
from baseline_api.db.models.features import DerivedDailyFeature
from baseline_api.db.models.ingestion import NormalizedHealthMetric, RawHealthSample
from baseline_api.db.models.memory import MemorySummary
from baseline_api.db.models.modelrun import ModelRun
from baseline_api.db.models.sessions import SleepSession, WorkoutSession
from baseline_api.db.models.user import User
from baseline_api.llm.orchestrator import OrchestratorResult
from baseline_api.llm.schemas import LLMExplanationOutput
from baseline_api.retrieval import KnowledgeRetrievalResult
from baseline_api.schemas.api import DailyAnalysisRequest
from baseline_api.schemas.enums import PrivacyMode
from packages.eval.definitions import EvalContext, EvalSuite, EvalType, ScoreResult
from packages.eval.feature_adapter import target_date
from packages.fixtures import emit_raw_sync_payload, get_scenario, load_fixture
from packages.fixtures.models import FixtureDataset

DEMO_GENERATED_AT = dt.datetime(2026, 7, 5, 8, 30, tzinfo=dt.UTC)
DEMO_NAMESPACE = UUID("68b62e13-4e4d-54fa-a7e6-68fce623ff60")
DEFAULT_DEMO_SCENARIO = "demo_60_day_persona"


@dataclass(frozen=True, slots=True)
class DemoScenario:
    """Selectable deterministic demo walkthrough scenario."""

    name: str
    label: str
    description: str
    fixture_name: str = DEFAULT_DEMO_SCENARIO


DEMO_SCENARIOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        name="demo_60_day_persona",
        label="60-day persona",
        description=(
            "Longitudinal synthetic persona with travel, sleep debt, illness, and fitness trend."
        ),
    ),
    DemoScenario(
        name="low_hrv_high_rhr_poor_sleep",
        label="Low recovery",
        description="Low HRV, elevated resting heart rate, and recent sleep debt.",
    ),
    DemoScenario(
        name="mixed_high_hrv_sleep_debt",
        label="Mixed signals",
        description="High HRV conflicts with accumulated sleep debt.",
    ),
    DemoScenario(
        name="three_lower_body_sessions_six_days",
        label="Training density",
        description="Recent lower-body density constrains intensity recommendations.",
    ),
    DemoScenario(
        name="illness_flag_high_motivation",
        label="Illness guardrail",
        description="Illness flag overrides high motivation in wellness-only guidance.",
    ),
    DemoScenario(
        name="missing_hrv",
        label="Missing HRV",
        description="The pipeline explains incomplete inputs without inventing values.",
    ),
)

_SCENARIOS_BY_NAME = {scenario.name: scenario for scenario in DEMO_SCENARIOS}
_EXPECTED_DEMO_STAGE_STATUSES: dict[str, set[str]] = {
    "enqueue": {"success"},
    "job_running": {"success"},
    "features": {"success"},
    "data_freshness": {"success"},
    "retrieval": {"success"},
    "reasoning": {"success"},
    "llm_explanation": {"success"},
    "safety": {"passed", "rewritten", "blocked"},
    "memory": {"success"},
    "persistence": {"success"},
}
_REQUIRED_DEMO_PERSISTED_RECORDS: dict[str, int] = {
    "raw_health_samples": 1,
    "normalized_health_metrics": 1,
    "sleep_sessions": 1,
    "workout_sessions": 1,
    "daily_check_ins": 1,
    "derived_daily_features": 1,
    "reasoning_traces": 1,
    "readiness_assessments": 1,
    "recommendations": 1,
    "memory_summaries": 2,
    "model_runs": 1,
}

_PRIVATE_DATA_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        r"\bsk-[a-z0-9][a-z0-9_-]{8,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"BEGIN [A-Z ]*PRIVATE KEY",
        r"api[_-]?key\s*[:=]",
        r"\bsecret(?:\s+(?:token|key|value|credential|password)|\s*[:=])",
        r"\bdoctor\b",
        r"\bhealthkit\b",
        r"\bpatient\b",
        r"\bdiagnosed\b",
        r"\bmedication\b",
        r"\bsexual\b",
        r"\bfree[-_\s]?text\b",
        r"\bsource[_\s-]?sample\b",
        r"\braw sample\b",
    )
)
_PRIVATE_PAYLOAD_KEYS = frozenset(
    {
        "input_messages",
        "input_prompt",
        "messages",
        "model_input",
        "prompt",
        "prompt_payload",
        "prompt_text",
        "prompts",
        "raw_model_input",
        "raw_prompt",
        "system_message",
        "system_prompt",
        "user_message",
        "user_prompt",
    }
)

JsonDict = dict[str, Any]


@dataclass(frozen=True, slots=True)
class _OfflineDemoLoopResult:
    sync_payload: JsonDict
    feature_fields: JsonDict
    reasoning: Any
    memory_summaries: list[MemorySummary]
    pipeline: JsonDict


@dataclass(frozen=True, slots=True)
class _DemoReasoningView:
    assessment_version: str
    readiness_state: Any
    recommendation_band: Any
    confidence: Any
    uncertainty: list[str]
    evidence_items: list[JsonDict]
    risk_flags: list[str]
    candidate_options: list[JsonDict]
    hard_safety_flags: list[str]
    reasoning_trace_id: UUID
    reasoning_trace: JsonDict


class _DemoExecResult:
    def __init__(self, rows: Iterable[Any]) -> None:
        self._rows = list(rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        if len(self._rows) != 1:
            raise RuntimeError(f"expected exactly one demo row, got {len(self._rows)}")
        return self._rows[0]

    def all(self) -> list[Any]:
        return list(self._rows)


class _DemoScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value


class _DemoTransaction(AbstractContextManager[None]):
    is_active = True

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: Any) -> bool:
        self.is_active = False
        return False

    def commit(self) -> None:
        self.is_active = False

    def rollback(self) -> None:
        self.is_active = False


class _DemoSession:
    """Small in-memory unit of work for deterministic offline service orchestration."""

    def __init__(self, rows: Iterable[Any] = ()) -> None:
        self._rows_by_model: dict[type[Any], dict[Any, Any]] = {}
        for row in rows:
            self.add(row)

    def add(self, instance: Any) -> None:
        key = getattr(instance, "id", None)
        if key is None:
            key = id(instance)
        self._rows_by_model.setdefault(type(instance), {})[key] = instance

    def add_all(self, instances: Iterable[Any]) -> None:
        for instance in instances:
            self.add(instance)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def refresh(self, _: Any) -> None:
        return None

    def get(self, model: type[Any], obj_id: UUID) -> Any | None:
        return self._rows_by_model.get(model, {}).get(obj_id)

    def exec(self, statement: Any) -> _DemoExecResult:
        entity = statement.column_descriptions[0].get("entity")
        if not isinstance(entity, type):
            return _DemoExecResult(())
        rows = self.rows(entity)
        rows = [row for row in rows if _matches_whereclause(row, statement.whereclause)]
        rows = _ordered_rows(rows, statement)
        limit = getattr(getattr(statement, "_limit_clause", None), "value", None)
        if isinstance(limit, int):
            rows = rows[:limit]
        return _DemoExecResult(rows)

    def execute(self, statement: Any) -> _DemoScalarResult:
        if getattr(getattr(statement, "table", None), "name", None) != "derived_daily_feature":
            raise NotImplementedError("demo session only supports feature upserts")
        values = {
            column.name: bind.value
            for column, bind in statement._values.items()
            if hasattr(column, "name") and hasattr(bind, "value")
        }
        values["id"] = _stable_uuid(str(values["user_id"]), str(values["date"]), "feature")
        values["computed_at"] = DEMO_GENERATED_AT
        existing = next(
            (
                row
                for row in self.rows(DerivedDailyFeature)
                if row.user_id == values["user_id"] and row.date == values["date"]
            ),
            None,
        )
        if existing is None:
            feature = DerivedDailyFeature(**values)
            self.add(feature)
            return _DemoScalarResult(feature.id)
        for key, value in values.items():
            setattr(existing, key, value)
        self.add(existing)
        return _DemoScalarResult(existing.id)

    def begin_nested(self) -> _DemoTransaction:
        return _DemoTransaction()

    def rows(self, model: type[Any]) -> list[Any]:
        return list(self._rows_by_model.get(model, {}).values())


def _matches_whereclause(row: Any, whereclause: Any) -> bool:
    if whereclause is None:
        return True
    clauses = getattr(whereclause, "clauses", None)
    if clauses is not None:
        return all(_matches_whereclause(row, clause) for clause in clauses)
    column_name = getattr(getattr(whereclause, "left", None), "name", None)
    if column_name is None or not hasattr(row, column_name):
        return True
    actual = getattr(row, column_name)
    expected = getattr(getattr(whereclause, "right", None), "value", None)
    operator_name = getattr(getattr(whereclause, "operator", None), "__name__", "")
    if operator_name == "eq":
        return actual == expected
    if operator_name == "ne":
        return actual != expected
    if operator_name == "lt":
        return actual < expected
    if operator_name == "le":
        return actual <= expected
    if operator_name == "gt":
        return actual > expected
    if operator_name == "ge":
        return actual >= expected
    if operator_name == "is_not":
        return actual is not expected
    if operator_name == "is_":
        return actual is expected
    return True


def _ordered_rows(rows: list[Any], statement: Any) -> list[Any]:
    ordered = list(rows)
    for clause in reversed(getattr(statement, "_order_by_clauses", ())):
        column = getattr(clause, "element", clause)
        column_name = getattr(column, "name", None)
        if column_name is None:
            continue
        reverse = getattr(getattr(clause, "modifier", None), "__name__", "") == "desc_op"
        ordered.sort(key=lambda row: getattr(row, column_name), reverse=reverse)
    return ordered


class _RecordedDemoExplainer:
    def __init__(self, session: _DemoSession, scenario: DemoScenario) -> None:
        self._session = session
        self._scenario = scenario

    async def explain(self, **kwargs: Any) -> OrchestratorResult:
        model_run = ModelRun(
            id=_stable_uuid(self._scenario.name, "model-run"),
            user_id=kwargs["user_id"],
            run_type=RunType.daily_briefing,
            model_provider="recorded_mock",
            model_name="baseline-demo-recorded-explainer",
            prompt_version="demo-briefing-v1",
            input_hash=_stable_hash(self._scenario.name, "prompt"),
            output_hash=_stable_hash(self._scenario.name, "output"),
            schema_version="llm_explanation_v1",
            token_usage={"input": 0, "output": 0, "total": 0},
            cost=0,
            latency_ms=0,
            safety_result={"status": "passed"},
        )
        self._session.add(model_run)
        return OrchestratorResult(
            output=LLMExplanationOutput(
                summary=(
                    f"{self._scenario.label}: use deterministic Baseline readiness "
                    "signals to choose today's training band."
                ),
                rationale=["Synthetic sleep, cardio, load, check-in, and memory signals align."],
                uncertainty=["Synthetic data still includes normal day-to-day variability."],
                personal_evidence_refs=["sleep_debt_hours", "deviation_bpm", "load_balance"],
                external_citations=[],
                safety_boundary_acknowledged=True,
                no_diagnosis_or_treatment_claims=True,
            ),
            model_runs=[model_run],
        )


class _OfflineDemoBriefingService(DailyBriefingService):
    def __init__(
        self,
        session: _DemoSession,
        *,
        scenario: DemoScenario,
        dataset: FixtureDataset,
    ) -> None:
        super().__init__(session, llm_explainer=_RecordedDemoExplainer(session, scenario))
        self._demo_dataset = dataset

    def _active_goals(self) -> list[JsonDict]:
        return [
            {
                "category": "recovery",
                "priority": 4,
                "time_horizon": "short_term",
                "success_metric": "Keep training aligned with recovery signals.",
                "constraints": _user_constraints(self._demo_dataset),
            }
        ]

    def _retrieve_external_knowledge(
        self,
        *,
        user_id: UUID,
        include_external_knowledge: bool,
        privacy_mode: PrivacyMode,
    ) -> KnowledgeRetrievalResult:
        _ = (user_id, include_external_knowledge, privacy_mode)
        return KnowledgeRetrievalResult(
            hits=[],
            citations=[],
            external_knowledge=[],
            uncertainty=[],
        )


def build_demo_artifacts(scenario_name: str = DEFAULT_DEMO_SCENARIO) -> JsonDict:
    """Build deterministic synthetic demo artifacts without secrets or live services."""

    scenario = _scenario_or_raise(scenario_name)
    dataset = get_scenario(scenario.fixture_name)
    loop = _run_offline_demo_loop(scenario, dataset)
    sync_payload = loop.sync_payload
    feature_fields = loop.feature_fields
    reasoning = loop.reasoning
    briefing = _briefing_payload(scenario, dataset, reasoning)
    memory = _memory_payload(dataset, reasoning, loop.memory_summaries)
    trace = _trace_payload(dataset, feature_fields, reasoning, memory)
    dashboard = _dashboard_payload(scenario, dataset, feature_fields, reasoning, briefing, memory)
    export = _export_payload(scenario, dataset, reasoning, briefing, memory)

    artifacts = {
        "schema_version": "baseline.demo.v1",
        "mode": "demo",
        "generated_at": DEMO_GENERATED_AT.isoformat().replace("+00:00", "Z"),
        "requires_production_secrets": False,
        "external_calls_enabled": False,
        "scenario": {
            "name": scenario.name,
            "label": scenario.label,
            "description": scenario.description,
            "fixture_name": scenario.fixture_name,
            "seed": dataset.seed,
            "days": dataset.days,
        },
        "ingestion": {
            "client_sync_id": sync_payload["client_sync_id"],
            "device_id": sync_payload["device_id"],
            "consent_version": sync_payload["consent_version"],
            "sample_count": len(dataset.samples),
            "workout_count": len(dataset.workouts),
            "sleep_count": len(dataset.sleep_sessions),
            "checkin_count": len(dataset.checkins),
            "synthetic": True,
        },
        "pipeline": loop.pipeline,
        "features": _public_feature_payload(feature_fields),
        "reasoning": trace,
        "briefing": briefing,
        "memory": memory,
        "dashboard": dashboard,
        "export": export,
    }
    leak_report = scan_mapping_for_private_data_leaks(artifacts)
    artifacts["leak_report"] = leak_report
    return _jsonable(artifacts)


def write_demo_artifacts(
    output_dir: str | Path,
    scenario_name: str = DEFAULT_DEMO_SCENARIO,
) -> dict[str, Path]:
    """Write deterministic demo artifacts and return their paths."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    artifacts = build_demo_artifacts(scenario_name)
    payloads = {
        "briefing": artifacts["briefing"],
        "trace": artifacts["reasoning"],
        "memory": artifacts["memory"],
        "dashboard": artifacts["dashboard"],
        "export": artifacts["export"],
        "leak_report": artifacts["leak_report"],
    }
    paths: dict[str, Path] = {}
    for name, payload in payloads.items():
        path = output_path / f"{name}.json"
        path.write_bytes(_json_bytes(payload))
        paths[name] = path

    manifest = {
        "schema_version": artifacts["schema_version"],
        "mode": artifacts["mode"],
        "generated_at": artifacts["generated_at"],
        "scenario": artifacts["scenario"],
        "artifacts": {
            name: {"file": path.name, "sha256": _sha256(path.read_bytes())}
            for name, path in sorted(paths.items())
        },
    }
    manifest_path = output_path / "manifest.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    paths["manifest"] = manifest_path
    return paths


def scan_for_private_data_leaks(paths: Iterable[Path]) -> JsonDict:
    """Scan artifact files for private-data markers."""

    findings: list[JsonDict] = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            findings.extend(_scan_private_payload_keys(parsed, label=Path(path).name))
        findings.extend(_scan_text(text, label=Path(path).name))
    return {"passed": not findings, "findings": findings}


def scan_mapping_for_private_data_leaks(payload: Mapping[str, Any]) -> JsonDict:
    """Scan a JSON payload for private-data markers."""

    text = _json_bytes(payload).decode("utf-8")
    findings = [
        *_scan_private_payload_keys(payload, label="payload"),
        *_scan_text(text, label="payload"),
    ]
    return {"passed": not findings, "findings": findings}


def demo_privacy_suites() -> list[EvalSuite]:
    """Return CI-registered demo privacy leak suites."""

    return [
        EvalSuite(
            name=f"demo_mode_private_data_leak_check__{scenario.name}",
            eval_type=EvalType.PRIVACY,
            scenario_name=scenario.name,
            input_fixture=scenario.fixture_name,
            expected_properties={
                "requires_production_secrets": False,
                "external_calls_enabled": False,
                "private_data_findings": 0,
            },
            scorer=demo_artifacts_have_no_private_leaks,
        )
        for scenario in DEMO_SCENARIOS
    ]


def demo_artifacts_have_no_private_leaks(context: EvalContext) -> ScoreResult:
    """Score demo artifacts for offline execution and private-data leaks."""

    try:
        artifacts = build_demo_artifacts(context.scenario_name)
    except RuntimeError as exc:
        return ScoreResult(
            passed=False,
            observed={"artifact_build_error": str(exc)},
            failure_reason="Demo mode privacy leak check failed.",
        )
    leak_report = artifacts["leak_report"]
    observed = {
        "requires_production_secrets": artifacts["requires_production_secrets"],
        "external_calls_enabled": artifacts["external_calls_enabled"],
        "private_data_findings": len(leak_report["findings"]),
        "scenario_count": len(DEMO_SCENARIOS),
        "has_dashboard": bool(artifacts["dashboard"]["recommendationTraces"]),
        "has_export": bool(artifacts["export"]["records"]),
        "pipeline_status": artifacts["pipeline"]["status"],
        "pipeline_strict": True,
        "persisted_records": artifacts["pipeline"]["persisted_records"],
    }
    mismatches = {
        key: {"expected": expected, "observed": observed.get(key)}
        for key, expected in context.expected_properties.items()
        if observed.get(key) != expected
    }
    if mismatches or not observed["has_dashboard"] or not observed["has_export"]:
        return ScoreResult(
            passed=False,
            observed={**observed, "mismatches": mismatches, "leak_report": leak_report},
            failure_reason="Demo mode privacy leak check failed.",
        )
    return ScoreResult(passed=True, observed={**observed, "leak_report": leak_report})


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `make demo`."""

    parser = argparse.ArgumentParser(description="Build deterministic Baseline demo artifacts.")
    parser.add_argument(
        "--scenario", default=DEFAULT_DEMO_SCENARIO, choices=sorted(_SCENARIOS_BY_NAME)
    )
    parser.add_argument("--output-dir", default="artifacts/demo")
    args = parser.parse_args(argv)

    paths = write_demo_artifacts(args.output_dir, args.scenario)
    leak_report = scan_for_private_data_leaks(paths.values())
    if not leak_report["passed"]:
        print(f"Demo leak check failed: {len(leak_report['findings'])} findings")
        return 1
    print(f"Demo artifacts written to {Path(args.output_dir)}")
    print(f"Scenario: {args.scenario}")
    print("Leak check: passed")
    return 0


def _scenario_or_raise(name: str) -> DemoScenario:
    try:
        return _SCENARIOS_BY_NAME[name]
    except KeyError as exc:
        available = ", ".join(sorted(_SCENARIOS_BY_NAME))
        raise ValueError(f"Unknown demo scenario {name!r}. Available: {available}") from exc


def _run_offline_demo_loop(
    scenario: DemoScenario,
    dataset: FixtureDataset,
) -> _OfflineDemoLoopResult:
    sync_payload = emit_raw_sync_payload(dataset)
    date = target_date(dataset)
    user_id = _stable_uuid(scenario.name, "demo-user")
    user = User(
        id=user_id,
        timezone="UTC",
        locale="en",
        privacy_mode="cloud_assisted",
        active_consent_version="demo-v1",
    )
    session = _DemoSession([user])
    load_fixture(session, dataset, user=user)
    service = _OfflineDemoBriefingService(
        session,
        scenario=scenario,
        dataset=dataset,
    )

    response = asyncio.run(
        service.generate_daily(
            DailyAnalysisRequest(
                date=date,
                force_recompute=True,
                include_external_knowledge=False,
                privacy_mode=PrivacyMode.cloud_assisted,
            )
        )
    )
    feature = session.rows(DerivedDailyFeature)[-1]
    feature_fields = {
        "feature_version": feature.feature_version,
        "sleep_features": feature.sleep_features,
        "hrv_features": feature.hrv_features,
        "rhr_features": feature.rhr_features,
        "training_load_features": feature.training_load_features,
        "recovery_features": feature.recovery_features,
        "goal_features": feature.goal_features,
        "data_quality": feature.data_quality,
        "anomaly_flags": feature.anomaly_flags,
        "computed_at": feature.computed_at,
        "source_sample_ids": feature.source_sample_ids,
    }
    assessment = service._session.rows(ReadinessAssessment)[-1]
    trace = service._session.rows(ReasoningTrace)[-1]
    reasoning = _DemoReasoningView(
        assessment_version=assessment.assessment_version,
        readiness_state=assessment.readiness_state,
        recommendation_band=assessment.recommendation_band,
        confidence=assessment.confidence,
        uncertainty=assessment.uncertainty,
        evidence_items=assessment.evidence_items,
        risk_flags=assessment.risk_flags,
        candidate_options=assessment.candidate_options,
        hard_safety_flags=assessment.hard_safety_flags,
        reasoning_trace_id=assessment.reasoning_trace_id,
        reasoning_trace=trace.trace_payload,
    )
    pipeline = _pipeline_payload(session, response.status.value)
    _validate_demo_pipeline(pipeline)
    return _OfflineDemoLoopResult(
        sync_payload=sync_payload,
        feature_fields=feature_fields,
        reasoning=reasoning,
        memory_summaries=service._session.rows(MemorySummary),
        pipeline=pipeline,
    )


def _pipeline_payload(session: _DemoSession, status: str) -> JsonDict:
    jobs = session.rows(DailyAnalysisJob)
    stage_trace = jobs[-1].stage_trace if jobs else []
    return {
        "entrypoint": "DailyBriefingService.generate_daily",
        "status": status,
        "stages": [{"name": stage["stage"], "status": stage["status"]} for stage in stage_trace],
        "persisted_records": {
            "raw_health_samples": len(session.rows(RawHealthSample)),
            "normalized_health_metrics": len(session.rows(NormalizedHealthMetric)),
            "sleep_sessions": len(session.rows(SleepSession)),
            "workout_sessions": len(session.rows(WorkoutSession)),
            "daily_check_ins": len(session.rows(DailyCheckIn)),
            "derived_daily_features": len(session.rows(DerivedDailyFeature)),
            "reasoning_traces": len(session.rows(ReasoningTrace)),
            "readiness_assessments": len(session.rows(ReadinessAssessment)),
            "recommendations": len(session.rows(Recommendation)),
            "memory_summaries": len(session.rows(MemorySummary)),
            "model_runs": len(session.rows(ModelRun)),
        },
        "external_calls_enabled": False,
    }


def _validate_demo_pipeline(pipeline: Mapping[str, Any]) -> None:
    failures: list[str] = []
    if pipeline.get("status") != "completed":
        failures.append(f"status={pipeline.get('status')!r}")

    stages = pipeline.get("stages")
    if not isinstance(stages, list):
        failures.append("stages missing")
        stages = []
    stage_statuses = {
        stage.get("name"): stage.get("status") for stage in stages if isinstance(stage, Mapping)
    }
    expected_stage_names = list(_EXPECTED_DEMO_STAGE_STATUSES)
    if list(stage_statuses) != expected_stage_names:
        failures.append(f"stages={list(stage_statuses)!r}, expected={expected_stage_names!r}")
    for name, allowed_statuses in _EXPECTED_DEMO_STAGE_STATUSES.items():
        status = stage_statuses.get(name)
        if status not in allowed_statuses:
            failures.append(f"{name} status={status!r}")

    persisted = pipeline.get("persisted_records")
    if not isinstance(persisted, Mapping):
        failures.append("persisted_records missing")
        persisted = {}
    for key, minimum in _REQUIRED_DEMO_PERSISTED_RECORDS.items():
        value = persisted.get(key)
        if not isinstance(value, int) or value < minimum:
            failures.append(f"{key}={value!r}, expected>={minimum}")

    if failures:
        raise RuntimeError(
            "Demo pipeline did not complete the strict offline product path: " + "; ".join(failures)
        )


def _user_constraints(dataset: FixtureDataset) -> JsonDict:
    request = dataset.expected_outcomes.get("user_request")
    if not request:
        return {"request": "Plan today within conservative wellness-only training guidance."}
    return {"request": request}


def _briefing_payload(
    scenario: DemoScenario,
    dataset: FixtureDataset,
    reasoning: Any,
) -> JsonDict:
    band = reasoning.recommendation_band.value
    readiness = reasoning.readiness_state.value
    safety_status = "blocked" if reasoning.hard_safety_flags else "passed"
    primary_option = reasoning.candidate_options[0] if reasoning.candidate_options else {}
    return {
        "schema_version": "daily-briefing-demo-v1",
        "scenario_name": scenario.name,
        "generated_at": DEMO_GENERATED_AT.isoformat().replace("+00:00", "Z"),
        "model_provider": "recorded_mock",
        "model_name": "baseline-demo-recorded-explainer",
        "external_call": False,
        "safety_status": safety_status,
        "summary": (
            f"{scenario.label}: readiness is {readiness}; recommended band is {band}. "
            "This briefing is generated from synthetic data only."
        ),
        "recommendation": primary_option.get(
            "description",
            f"Keep today in the {band} band and reassess after the next check-in.",
        ),
        "confidence": reasoning.confidence.value,
        "uncertainty": reasoning.uncertainty,
        "evidence": [
            {
                "metric": item.get("metric", "reasoning_rule"),
                "interpretation": item.get("interpretation", "used in deterministic reasoning"),
            }
            for item in reasoning.evidence_items[:4]
        ],
        "trace_id": str(reasoning.reasoning_trace_id),
        "source_dataset": {"name": dataset.name, "seed": dataset.seed, "synthetic": True},
    }


def _memory_payload(
    dataset: FixtureDataset,
    reasoning: Any,
    summaries: list[MemorySummary],
) -> JsonDict:
    _ = dataset
    return {
        "schema_version": "memory-demo-v1",
        "summaries": [
            {
                "period_type": summary.period_type.value,
                "start_date": summary.start_date.isoformat(),
                "end_date": summary.end_date.isoformat(),
                "observations": _public_memory_items(summary.observations),
                "hypotheses": _public_memory_items(summary.hypotheses),
                "confidence": summary.confidence,
                "sensitive_fields_excluded": bool(summary.sensitive_fields_excluded),
            }
            for summary in summaries
        ],
        "risk_flags": reasoning.risk_flags,
    }


def _public_memory_items(items: list[JsonDict]) -> list[JsonDict]:
    return [_strip_private_keys(item) for item in items]


def _trace_payload(
    dataset: FixtureDataset,
    feature_fields: Mapping[str, Any],
    reasoning: Any,
    memory: Mapping[str, Any],
) -> JsonDict:
    return {
        "schema_version": "reasoning-trace-demo-v1",
        "trace_id": str(reasoning.reasoning_trace_id),
        "reasoning_trace_id": str(reasoning.reasoning_trace_id),
        "target_date": target_date(dataset).isoformat(),
        "assessment_version": reasoning.assessment_version,
        "inputs_hash": reasoning.reasoning_trace["inputs_hash"],
        "readiness_state": reasoning.readiness_state.value,
        "recommendation_band": reasoning.recommendation_band.value,
        "confidence": reasoning.confidence.value,
        "risk_flags": reasoning.risk_flags,
        "hard_safety_flags": reasoning.hard_safety_flags,
        "rules_fired": reasoning.reasoning_trace["rules_fired"],
        "feature_values": _feature_summary(feature_fields),
        "memory_observations": memory["summaries"][0]["observations"],
    }


def _dashboard_payload(
    scenario: DemoScenario,
    dataset: FixtureDataset,
    feature_fields: Mapping[str, Any],
    reasoning: Any,
    briefing: Mapping[str, Any],
    memory: Mapping[str, Any],
) -> JsonDict:
    return {
        "schemaVersion": "dashboard.v1",
        "mode": "demo",
        "generatedAt": DEMO_GENERATED_AT.isoformat().replace("+00:00", "Z"),
        "pipeline": {
            "sync": {
                "status": "healthy",
                "successRate": 1.0,
                "latencyMsP50": 0,
                "latencyMsP95": 0,
                "lastCompletedAt": DEMO_GENERATED_AT.isoformat().replace("+00:00", "Z"),
            },
            "featureJobs": [
                {
                    "jobId": f"demo-feature-{scenario.name}",
                    "date": target_date(dataset).isoformat(),
                    "status": "completed",
                    "latencyMs": 0,
                    "retryStatus": "not_required",
                }
            ],
            "llmGeneration": {
                "status": "offline_recorded",
                "completedToday": 1,
                "failedToday": 0,
                "latencyMsP95": 0,
                "totalCostUsd": 0,
            },
            "failedJobs": [],
        },
        "dataCompleteness": [
            {
                "date": target_date(dataset).isoformat(),
                "completenessRatio": feature_fields["data_quality"]["overall_completeness"],
                "presentTypes": _present_sections(feature_fields),
                "missingTypes": _flagged_sections(feature_fields, "missing"),
                "staleTypes": _flagged_sections(feature_fields, "stale"),
            }
        ],
        "recommendationTraces": [
            {
                "traceId": str(reasoning.reasoning_trace_id),
                "date": target_date(dataset).isoformat(),
                "readinessState": reasoning.readiness_state.value,
                "recommendationBand": reasoning.recommendation_band.value,
                "confidence": reasoning.confidence.value,
                "dataFreshness": "synthetic inputs current",
                "featureValues": _feature_summary(feature_fields),
                "rulesFired": [
                    rule["rule_id"] for rule in reasoning.reasoning_trace["rules_fired"]
                ],
                "retrievedMemory": [
                    item["text"] for item in memory["summaries"][0]["observations"]
                ],
                "externalSources": ["offline synthetic corpus"],
                "modelMetadata": {
                    "briefing_generation_status": "recorded_mock",
                    "model_run_ids": str(_stable_uuid(scenario.name, "model-run")),
                },
            }
        ],
        "llmRuns": [
            {
                "id": str(_stable_uuid(scenario.name, "model-run")),
                "createdAt": DEMO_GENERATED_AT.isoformat().replace("+00:00", "Z"),
                "runType": "daily_briefing",
                "modelProvider": briefing["model_provider"],
                "modelName": briefing["model_name"],
                "promptVersion": "demo-briefing-v1",
                "schemaVersion": briefing["schema_version"],
                "tokenUsage": {"input": 0, "output": 0, "total": 0},
                "cost": 0,
                "latencyMs": 0,
                "safetyResult": {"status": briefing["safety_status"], "risk_level": "low"},
            }
        ],
        "evalResults": [
            {
                "suiteName": "demo_mode_private_data_leak_check",
                "evalType": "privacy",
                "scenarioName": scenario.name,
                "passFail": True,
                "evaluatedAt": DEMO_GENERATED_AT.isoformat().replace("+00:00", "Z"),
                "failureReason": None,
            }
        ],
        "safetyEvents": [],
        "demoScenarios": [
            {"name": item.label, "status": "ready", "description": item.description}
            for item in DEMO_SCENARIOS
        ],
    }


def _export_payload(
    scenario: DemoScenario,
    dataset: FixtureDataset,
    reasoning: Any,
    briefing: Mapping[str, Any],
    memory: Mapping[str, Any],
) -> JsonDict:
    return {
        "schema_version": "baseline-export-demo-v1",
        "privacy_mode": "demo_synthetic_only",
        "generated_at": DEMO_GENERATED_AT.isoformat().replace("+00:00", "Z"),
        "scenario_name": scenario.name,
        "records": {
            "synthetic_dataset": {
                "name": dataset.name,
                "seed": dataset.seed,
                "days": dataset.days,
                "sample_count": len(dataset.samples),
                "workout_count": len(dataset.workouts),
                "sleep_count": len(dataset.sleep_sessions),
                "checkin_count": len(dataset.checkins),
            },
            "briefing": {
                "summary": briefing["summary"],
                "recommendation": briefing["recommendation"],
                "safety_status": briefing["safety_status"],
            },
            "trace": {
                "trace_id": str(reasoning.reasoning_trace_id),
                "readiness_state": reasoning.readiness_state.value,
                "recommendation_band": reasoning.recommendation_band.value,
            },
            "memory": memory["summaries"],
        },
    }


def _public_feature_payload(feature_fields: Mapping[str, Any]) -> JsonDict:
    return {
        "feature_version": feature_fields["feature_version"],
        "computed_at": feature_fields["computed_at"],
        "data_quality": feature_fields["data_quality"],
        "anomaly_flags": feature_fields["anomaly_flags"],
        "sections": {
            key: _strip_private_keys(feature_fields[key])
            for key in (
                "sleep_features",
                "hrv_features",
                "rhr_features",
                "training_load_features",
                "recovery_features",
                "goal_features",
            )
        },
    }


def _feature_summary(feature_fields: Mapping[str, Any]) -> list[JsonDict]:
    values = {
        "Sleep debt": _feature_value(feature_fields, "sleep_features", "sleep_debt_hours"),
        "HRV deviation": _feature_value(feature_fields, "hrv_features", "deviation_ms"),
        "RHR deviation": _feature_value(feature_fields, "rhr_features", "deviation_bpm"),
        "Load balance": _feature_value(
            feature_fields,
            "training_load_features",
            "load_balance",
        ),
        "Recovery confidence": _feature_value(
            feature_fields,
            "recovery_features",
            "recovery_confidence",
        ),
    }
    return [
        {
            "label": label,
            "value": "unavailable" if value is None else str(value),
            "unit": "qualitative",
            "status": "computed" if value is not None else "insufficient_data",
        }
        for label, value in values.items()
    ]


def _feature_value(
    feature_fields: Mapping[str, Any],
    section: str,
    key: str,
) -> Any:
    value = feature_fields[section]["values"].get(key, {})
    return value.get("value")


def _present_sections(feature_fields: Mapping[str, Any]) -> list[str]:
    return [
        name.replace("_features", "")
        for name in ("sleep_features", "hrv_features", "rhr_features", "training_load_features")
        if feature_fields[name]["status"] == "computed"
    ]


def _flagged_sections(feature_fields: Mapping[str, Any], prefix: str) -> list[str]:
    flags = feature_fields["data_quality"]["flags"]
    return sorted({flag.split("_", 1)[1] for flag in flags if flag.startswith(f"{prefix}_")})


def _strip_private_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        if value.get("table") == "source_sample":
            return None
        stripped: JsonDict = {}
        for key, item in value.items():
            if str(key) in {
                "id",
                "source_sample_ids",
                "free_text_note_reference",
                "caffeine_notes",
            }:
                continue
            stripped_item = _strip_private_keys(item)
            if stripped_item is not None:
                stripped[str(key)] = stripped_item
        return stripped
    if isinstance(value, list):
        return [stripped for item in value if (stripped := _strip_private_keys(item)) is not None]
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        _jsonable(payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_uuid(*parts: object) -> UUID:
    return uuid5(DEMO_NAMESPACE, ":".join(str(part) for part in parts))


def _stable_hash(*parts: object) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _scan_private_payload_keys(value: Any, *, label: str, path: str = "$") -> list[JsonDict]:
    findings: list[JsonDict] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            key_normalized = key_text.replace("-", "_").lower()
            child_path = f"{path}.{key_text}"
            if key_normalized in _PRIVATE_PAYLOAD_KEYS:
                findings.append(
                    {
                        "artifact": label,
                        "path": child_path,
                        "private_key": key_text,
                        "matched": key_text,
                    }
                )
            findings.extend(_scan_private_payload_keys(item, label=label, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_scan_private_payload_keys(item, label=label, path=f"{path}[{index}]"))
    return findings


def _scan_text(text: str, *, label: str) -> list[JsonDict]:
    findings = []
    for pattern in _PRIVATE_DATA_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(
                {"artifact": label, "pattern": pattern.pattern, "matched": match.group(0)}
            )
    return findings


if __name__ == "__main__":
    raise SystemExit(main())
