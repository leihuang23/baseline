"""Reasoning golden scenario evals for deterministic readiness behavior."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from baseline_api.features.assembler import assemble_daily_features
from baseline_api.reasoning.engine import (
    BAND_RANK,
    REQUEST_ROUTE_SAFETY_REDIRECT,
    REQUEST_ROUTE_TRAINING,
    RISK_FLAG_BAND_CEILINGS,
    ReasoningInput,
    assess_readiness,
)
from packages.eval.definitions import EvalContext, EvalSuite, EvalType, ScoreResult
from packages.eval.feature_adapter import (
    hrv_inputs,
    rhr_inputs,
    sleep_inputs,
    target_date,
    vo2_inputs,
    workout_inputs,
)
from packages.fixtures import GOLDEN_SCENARIO_NAMES, list_scenarios
from packages.fixtures.models import CheckInRecord, FixtureDataset

REASONING_SUITE_PREFIX = "reasoning_golden_"
BASE_STRUCTURAL_PROPERTIES = (
    "evidence_present",
    "confidence_present",
    "uncertainty_present",
    "trace_present",
)


@dataclass(frozen=True, slots=True)
class ReasoningScenarioCase:
    """A fixture-backed reasoning eval case and its structural expectations."""

    scenario_name: str
    expected_properties: Mapping[str, Any]

    @property
    def suite_name(self) -> str:
        return f"{REASONING_SUITE_PREFIX}{self.scenario_name}"


def reasoning_scenario_suites() -> list[EvalSuite]:
    """Build one registered reasoning suite per golden scenario fixture."""

    return [
        EvalSuite(
            name=case.suite_name,
            eval_type=EvalType.REASONING,
            scenario_name=case.scenario_name,
            input_fixture=case.scenario_name,
            expected_properties=case.expected_properties,
            scorer=reasoning_properties_match,
        )
        for case in REASONING_SCENARIO_CASES
    ]


def reasoning_properties_match(context: EvalContext) -> ScoreResult:
    """Assert reasoning outputs by structural properties, never exact prose."""

    features = _assembled_feature_fields(context.fixture)
    result = assess_readiness(
        ReasoningInput(
            target_date=target_date(context.fixture),
            features=features,
            active_goals=_sequence_property(context.expected_properties, "active_goals"),
            recent_memory=_sequence_property(context.expected_properties, "recent_memory"),
            user_constraints=_scenario_user_constraints(
                context.expected_properties,
                context.fixture,
            ),
            daily_check_in=_target_checkin_payload(context.fixture),
            include_external_knowledge=bool(
                context.expected_properties.get("include_external_knowledge", False)
            ),
        )
    )
    observed = _observed_output(result, features, context.fixture)
    failures = _structural_failures(observed)
    failures.extend(_expected_property_failures(context.expected_properties, observed))

    if failures:
        return ScoreResult(
            passed=False,
            observed=observed,
            failure_reason="Reasoning property failures: " + "; ".join(failures),
        )
    return ScoreResult(passed=True, observed=observed)


def _assembled_feature_fields(dataset: FixtureDataset) -> dict[str, Any]:
    bundle = assemble_daily_features(
        target_date(dataset),
        sleep_sessions=sleep_inputs(dataset),
        hrv_samples=hrv_inputs(dataset),
        rhr_samples=rhr_inputs(dataset),
        workouts=workout_inputs(dataset),
        vo2_samples=vo2_inputs(dataset),
        computed_at=dt.datetime(2026, 1, 20, 8, 0, 0, tzinfo=dt.UTC),
    )
    return bundle.to_derived_daily_feature_fields()


def _target_checkin_payload(dataset: FixtureDataset) -> dict[str, Any] | None:
    checkin = _target_checkin(dataset)
    if checkin is None:
        return None
    return {
        "energy_score": checkin.energy_score,
        "soreness_score": checkin.soreness_score,
        "perceived_recovery_score": checkin.perceived_recovery_score,
        "illness_flag": checkin.illness_flag,
        "injury_flag": checkin.injury_flag,
        "travel_flag": checkin.travel_flag,
    }


def _target_checkin(dataset: FixtureDataset) -> CheckInRecord | None:
    date = target_date(dataset)
    for checkin in dataset.checkins:
        if checkin.date == date:
            return checkin
    return None


def _observed_output(
    result: Any,
    features: Mapping[str, Any],
    dataset: FixtureDataset,
) -> dict[str, Any]:
    return {
        "readiness_state": result.readiness_state.value,
        "recommendation_band": result.recommendation_band.value,
        "confidence": result.confidence.value,
        "evidence_metrics": [item.get("metric") for item in result.evidence_items],
        "evidence_count": len(result.evidence_items),
        "risk_flags": result.risk_flags,
        "hard_safety_flags": result.hard_safety_flags,
        "uncertainty": result.uncertainty,
        "follow_up_questions": result.follow_up_questions,
        "goal_tradeoff_goals": [item.get("goal") for item in result.goal_tradeoffs],
        "candidate_option_bands": [
            item.get("recommendation_band") for item in result.candidate_options
        ],
        "rules_fired": [rule.get("rule_id") for rule in result.reasoning_trace["rules_fired"]],
        "reasoning_trace_id": str(result.reasoning_trace_id),
        "trace_reasoning_trace_id": result.reasoning_trace.get("reasoning_trace_id"),
        "trace_inputs_hash_present": bool(result.reasoning_trace.get("inputs_hash")),
        "trace_recommendation_band": result.reasoning_trace.get("recommendation_band"),
        "data_quality_flags": features["data_quality"]["flags"],
        "data_quality_overall_completeness": features["data_quality"]["overall_completeness"],
        "fixture_labels": dataset.labels,
        "fixture_expected_outcomes": dataset.expected_outcomes,
        "user_request": _user_request(dataset),
        "safety_route": result.reasoning_trace.get("request_route", REQUEST_ROUTE_TRAINING),
        "training_recommendation_suppressed": (
            result.reasoning_trace.get("request_route") == REQUEST_ROUTE_SAFETY_REDIRECT
            and result.recommendation_band.value in {"rest", "insufficient_data"}
        ),
    }


def _structural_failures(observed: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if observed["evidence_count"] <= 0:
        failures.append("evidence must be present")
    if not observed["confidence"]:
        failures.append("confidence must be present")
    if not observed["uncertainty"]:
        failures.append("uncertainty must be present")
    if observed["reasoning_trace_id"] != observed["trace_reasoning_trace_id"]:
        failures.append("reasoning trace id must round-trip")
    if not observed["trace_inputs_hash_present"]:
        failures.append("reasoning trace must include an input hash")
    if observed["recommendation_band"] != observed["trace_recommendation_band"]:
        failures.append("trace recommendation band must match output")

    for risk_flag in observed["risk_flags"]:
        ceiling = RISK_FLAG_BAND_CEILINGS.get(str(risk_flag))
        if ceiling is None:
            continue
        if _band_rank(str(observed["recommendation_band"])) > BAND_RANK[ceiling]:
            failures.append(f"{risk_flag} must cap the recommendation band")
    return failures


def _expected_property_failures(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    _expect_equal(failures, expected, observed, "expected_readiness_state", "readiness_state")
    _expect_equal(
        failures,
        expected,
        observed,
        "expected_recommendation_band",
        "recommendation_band",
    )
    _expect_equal(failures, expected, observed, "expected_confidence", "confidence")
    _expect_subset(failures, expected, observed, "required_risk_flags", "risk_flags")
    _expect_subset(
        failures,
        expected,
        observed,
        "required_hard_safety_flags",
        "hard_safety_flags",
    )
    _expect_subset(failures, expected, observed, "required_rules", "rules_fired")
    _expect_subset(
        failures,
        expected,
        observed,
        "required_data_quality_flags",
        "data_quality_flags",
    )
    _expect_subset(failures, expected, observed, "required_fixture_labels", "fixture_labels")
    _expect_subset(
        failures,
        expected,
        observed,
        "required_goal_tradeoff_goals",
        "goal_tradeoff_goals",
    )
    _expect_subset(
        failures,
        expected,
        observed,
        "required_evidence_metrics",
        "evidence_metrics",
    )

    forbidden_bands = set(_strings_property(expected, "forbidden_recommendation_bands"))
    if observed["recommendation_band"] in forbidden_bands:
        failures.append(f"recommendation_band must not be {observed['recommendation_band']}")

    forbidden_flags = set(_strings_property(expected, "forbidden_risk_flags"))
    present_forbidden_flags = forbidden_flags & {str(flag) for flag in observed["risk_flags"]}
    if present_forbidden_flags:
        failures.append(f"forbidden risk flags present: {sorted(present_forbidden_flags)}")

    max_band = expected.get("max_recommendation_band")
    if isinstance(max_band, str) and _band_rank(str(observed["recommendation_band"])) > _band_rank(
        max_band
    ):
        failures.append(f"recommendation_band must be no higher than {max_band}")

    if expected.get("require_conflict") and "conflicting_signals" not in observed["risk_flags"]:
        failures.append("conflicting_signals risk flag must be present")

    if expected.get("require_missing_or_stale_uncertainty"):
        uncertainty_text = " ".join(str(item).lower() for item in observed["uncertainty"])
        if "missing" not in uncertainty_text and "stale" not in uncertainty_text:
            failures.append("uncertainty must disclose missing or stale inputs")
        if observed["confidence"] == "high":
            failures.append("missing or stale inputs must reduce confidence below high")

    missing_metric = expected.get("missing_metric_no_evidence")
    if isinstance(missing_metric, str):
        evidence_metrics = [str(metric) for metric in observed["evidence_metrics"]]
        if any(metric.startswith(missing_metric) for metric in evidence_metrics):
            failures.append(f"missing {missing_metric} must not produce metric evidence")

    if expected.get("require_follow_up_questions") and not observed["follow_up_questions"]:
        failures.append("follow-up questions must be present")

    if expected.get("require_safety_route"):
        route = observed["safety_route"]
        if route != REQUEST_ROUTE_SAFETY_REDIRECT:
            failures.append(f"medical-boundary request must route to safety, got {route}")
        if not observed["training_recommendation_suppressed"]:
            failures.append("medical-boundary request must suppress training recommendation")

    return failures


def _expect_equal(
    failures: list[str],
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
    expected_key: str,
    observed_key: str,
) -> None:
    expected_value = expected.get(expected_key)
    if isinstance(expected_value, str) and observed.get(observed_key) != expected_value:
        failures.append(f"{observed_key} expected {expected_value}, got {observed[observed_key]}")


def _expect_subset(
    failures: list[str],
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
    expected_key: str,
    observed_key: str,
) -> None:
    required = set(_strings_property(expected, expected_key))
    actual = {str(item) for item in observed[observed_key]}
    missing = required - actual
    if missing:
        failures.append(f"{observed_key} missing {sorted(missing)}")


def _mapping_property(expected: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = expected.get(key)
    return value if isinstance(value, Mapping) else {}


def _scenario_user_constraints(
    expected: Mapping[str, Any],
    dataset: FixtureDataset,
) -> Mapping[str, Any]:
    constraints = dict(_mapping_property(expected, "user_constraints"))
    user_request = _user_request(dataset)
    if user_request is not None and "user_request" not in constraints:
        constraints["user_request"] = user_request
    return constraints


def _sequence_property(expected: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = expected.get(key)
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()


def _strings_property(expected: Mapping[str, Any], key: str) -> list[str]:
    value = expected.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [str(item) for item in value]


def _band_rank(value: str) -> int:
    for band, rank in BAND_RANK.items():
        if band.value == value:
            return rank
    raise ValueError(f"Unknown recommendation band {value!r}")


def _user_request(dataset: FixtureDataset) -> str | None:
    request = dataset.expected_outcomes.get("user_request")
    return request if isinstance(request, str) else None


def _case(scenario_name: str, **expected_properties: Any) -> ReasoningScenarioCase:
    return ReasoningScenarioCase(
        scenario_name=scenario_name,
        expected_properties={
            "required_structural_properties": BASE_STRUCTURAL_PROPERTIES,
            **expected_properties,
        },
    )


def _variant_cases() -> tuple[ReasoningScenarioCase, ...]:
    evidence_by_family = {
        "sleep": ["sleep_debt_hours"],
        "training": ["acute_chronic_ratio"],
        "recovery": ["hrv_deviation_pct", "rhr_deviation_pct"],
    }
    cases: list[ReasoningScenarioCase] = []
    for scenario_name in list_scenarios():
        if scenario_name in GOLDEN_SCENARIO_NAMES or scenario_name == "demo_60_day_persona":
            continue
        family = scenario_name.split("_", 1)[0]
        cases.append(
            _case(
                scenario_name,
                required_fixture_labels=["variant"],
                required_evidence_metrics=evidence_by_family[family],
            )
        )
    return tuple(
        cases
    )


REASONING_NAMED_SCENARIO_CASES: tuple[ReasoningScenarioCase, ...] = (
    _case(
        "high_hrv_good_sleep_low_load",
        expected_readiness_state="high",
        expected_recommendation_band="hard_training_ok",
        expected_confidence="high",
        forbidden_risk_flags=["hard_safety_illness", "data_quality_low_readiness"],
    ),
    _case(
        "low_hrv_high_rhr_poor_sleep",
        expected_readiness_state="low",
        max_recommendation_band="recovery",
        required_risk_flags=["high_sleep_debt", "poor_subjective_recovery"],
        required_rules=["high_sleep_debt", "poor_subjective_recovery"],
    ),
    _case(
        "mixed_high_hrv_sleep_debt",
        expected_readiness_state="mixed",
        max_recommendation_band="easy",
        required_risk_flags=["high_sleep_debt", "conflicting_signals"],
        required_rules=["conflict_favorable_hrv_sleep_debt"],
        require_conflict=True,
    ),
    _case(
        "three_lower_body_sessions_six_days",
        max_recommendation_band="moderate_or_upper_body",
        required_risk_flags=["high_training_density"],
        required_rules=["high_training_density"],
    ),
    _case(
        "illness_flag_high_motivation",
        expected_readiness_state="low",
        expected_recommendation_band="rest",
        required_risk_flags=["hard_safety_illness", "conflicting_signals"],
        required_hard_safety_flags=["illness"],
        require_conflict=True,
    ),
    _case(
        "missing_hrv",
        max_recommendation_band="moderate",
        forbidden_recommendation_bands=["hard_training_ok"],
        required_risk_flags=["missing_or_stale_data"],
        required_data_quality_flags=["missing_heart_rate_variability"],
        require_missing_or_stale_uncertainty=True,
        missing_metric_no_evidence="hrv",
    ),
    _case(
        "stale_sleep",
        max_recommendation_band="moderate",
        required_risk_flags=["missing_or_stale_data"],
        required_rules=["sleep_data_missing"],
        required_data_quality_flags=["stale_sleep"],
        require_missing_or_stale_uncertainty=True,
        require_follow_up_questions=True,
        missing_metric_no_evidence="sleep",
    ),
    _case(
        "vo2_improving_recovery_declining",
        max_recommendation_band="recovery",
        required_risk_flags=["poor_subjective_recovery"],
        active_goals=[
            {"category": "vo2_max", "priority": 1},
            {"category": "recovery", "priority": 2},
        ],
        required_goal_tradeoff_goals=["vo2_max", "recovery"],
    ),
    _case(
        "cognitive_priority_week",
        active_goals=[
            {"category": "cognitive_performance", "priority": 1},
            {"category": "vo2_max", "priority": 3},
        ],
        user_constraints={"intended_intensity": "hard"},
        required_goal_tradeoff_goals=["cognitive_performance", "vo2_max"],
    ),
    _case(
        "medical_diagnosis_request",
        required_hard_safety_flags=["illness"],
        require_safety_route=True,
    ),
)

REASONING_SCENARIO_CASES: tuple[ReasoningScenarioCase, ...] = (
    *REASONING_NAMED_SCENARIO_CASES,
    *_variant_cases(),
)
