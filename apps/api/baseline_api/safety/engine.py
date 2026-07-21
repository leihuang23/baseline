"""Deterministic post-generation safety policy engine."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self, cast

from baseline_api.schemas.enums import SafetyStatus
from baseline_api.schemas.recommendation import RecommendationContract, RecommendationSummary

if TYPE_CHECKING:
    from baseline_api.llm.schemas import LLMExplanationOutput, PromptInputs

CategoryAction = Literal["refuse", "redirect"]

MEDICAL_ADJACENT_TERMS = (
    "symptom",
    "symptoms",
    "illness",
    "injury",
    "pain",
    "medical",
    "medication",
    "supplement",
    "doctor",
    "clinician",
    "pharmacist",
    "physical therapist",
    "chest pain",
    "shortness of breath",
    "can't breathe",
    "depression",
    "anxiety",
    "drug",
    "dose",
    "capsule",
    "tablet",
)

HIGH_RISK_OUTPUT_RULES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "diagnosis",
        (
            re.compile(r"\byou (?:have|likely have|probably have|definitely have)\b", re.I),
            re.compile(r"\bthis (?:confirms|proves|means|indicates) you have\b", re.I),
            re.compile(r"\bdiagnosis is\b", re.I),
            re.compile(
                r"\bthis is (?:depression|anxiety|bipolar|ptsd|anemia|infection)\b",
                re.I,
            ),
            re.compile(
                r"\byou (?:are|seem|look) "
                r"(?:anemic|diabetic|depressed|infected|hypothyroid|overtrained)\b",
                re.I,
            ),
        ),
    ),
    (
        "treatment_instructions",
        (
            re.compile(r"\btreat (?:the|your|my)?\s*(?:condition|disease|infection)\b", re.I),
            re.compile(r"\btherapy plan\b", re.I),
            re.compile(r"\bmedical protocol\b", re.I),
            re.compile(r"\buse antibiotics\b", re.I),
        ),
    ),
    (
        "medication_supplement_dosing",
        (
            re.compile(r"\b\d+(?:\.\d+)?\s*mg\b", re.I),
            re.compile(r"\b(?:start|stop|increase|decrease) taking\b", re.I),
            re.compile(r"\bsupplement stack\b", re.I),
            re.compile(
                r"\b(?:take|use|combine|start|stop|increase|decrease)\b.*"
                r"\b(?:capsules?|tablets?|pills?|doses?|ibuprofen|acetaminophen|"
                r"paracetamol|nsaid|beta blocker|antidepressant|medication|drug|"
                r"supplement|magnesium|iron|antibiotics?)\b",
                re.I,
            ),
            re.compile(r"\bdrug interaction\b", re.I),
        ),
    ),
    (
        "injury_rehab_protocols",
        (
            re.compile(r"\breturn[- ]to[- ]play\b", re.I),
            re.compile(r"\brehab protocol\b", re.I),
            re.compile(r"\brun through pain\b", re.I),
            re.compile(r"\b(?:knee|ankle|hip|shoulder|back|tendon|sprain) pain\b.*\bdaily\b", re.I),
            re.compile(r"\bdo [\w -]+(?:squats?|stretches|raises|drills?) daily\b", re.I),
        ),
    ),
    (
        "sexual_dysfunction_dx_tx",
        (
            re.compile(r"\berectile dysfunction\b", re.I),
            re.compile(r"\btestosterone treatment\b", re.I),
            re.compile(r"\bfertility treatment\b", re.I),
        ),
    ),
    (
        "trend_proves_condition",
        (
            re.compile(r"\btrend proves\b", re.I),
            re.compile(r"\bproves you have\b", re.I),
            re.compile(r"\brules out\b", re.I),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SafetyPolicyCategory:
    """One refused or redirected policy category from the JSON policy."""

    id: str
    action: CategoryAction
    trigger_patterns: tuple[str, ...]
    safe_redirect: str
    escalation: str
    required_disclaimers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SafetyResult:
    """Authoritative verdict from the post-generation safety gate."""

    status: SafetyStatus
    policy_version: str
    triggered_categories: tuple[str, ...]
    request_categories: tuple[str, ...]
    output_categories: tuple[str, ...]
    required_disclaimers: tuple[str, ...]
    original_output: str
    safe_output: str
    safety_note: str
    action: str
    reason: str
    evaluated_at: dt.datetime

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable safety result for ModelRun telemetry."""

        return {
            "status": self.status.value,
            "policy_version": self.policy_version,
            "triggered_categories": list(self.triggered_categories),
            "request_categories": list(self.request_categories),
            "output_categories": list(self.output_categories),
            "original_classification": {
                "request": list(self.request_categories),
                "output": list(self.output_categories),
            },
            "final_classification": {
                "status": self.status.value,
                "triggered_categories": list(self.triggered_categories),
            },
            "required_disclaimers": list(self.required_disclaimers),
            "action": self.action,
            "reason": self.reason,
            "rewrite_applied": self.safe_output != self.original_output,
            "safety_note": self.safety_note,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class SafetyPolicyEngine:
    """Load and enforce the machine-readable safety policy as a hard gate."""

    def __init__(
        self,
        *,
        policy_version: str,
        disclaimers: Mapping[str, str],
        categories: Sequence[SafetyPolicyCategory],
    ) -> None:
        self.policy_version = policy_version
        self.disclaimers = dict(disclaimers)
        self.categories = tuple(categories)

    @classmethod
    def from_default_policy(cls) -> Self:
        """Load the versioned safety policy artifact."""

        policy_path = (
            Path(__file__).resolve().parents[4] / "packages/eval/policy/safety_policy.json"
        )
        return cls.from_policy_path(policy_path)

    @classmethod
    def from_policy_path(cls, policy_path: Path) -> Self:
        """Load a safety engine from a machine-readable policy JSON file."""

        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Safety policy must be a JSON object")

        categories = [
            _category_from_payload(cast("Mapping[str, Any]", category))
            for category in _sequence(payload, "refusal_categories")
        ]
        disclaimers = {
            str(key): str(value) for key, value in _mapping(payload, "required_disclaimers").items()
        }
        return cls(
            policy_version=str(payload["policy_version"]),
            disclaimers=disclaimers,
            categories=categories,
        )

    def evaluate(self, *, request_text: str, generated_text: str) -> SafetyResult:
        """Classify request and generated output, returning the enforced verdict."""

        request_categories = self._classify(request_text)
        output_categories = self._classify(generated_text) + self._high_risk_output_categories(
            generated_text
        )
        output_categories = _dedupe(output_categories)

        triggered_categories = _dedupe(request_categories + output_categories)

        if triggered_categories:
            status = self._status_for_categories(triggered_categories)
            required_disclaimers = self._required_disclaimers(triggered_categories)
            if request_categories or status is SafetyStatus.escalated:
                safe_output = self._blocked_or_redirected_output(triggered_categories)
                reason = (
                    "request_escalated" if status is SafetyStatus.escalated else "request_refused"
                )
            else:
                status = SafetyStatus.rewritten
                safe_output = self._high_risk_rewrite(triggered_categories)
                reason = "output_rewritten"
            return self._result(
                status=status,
                request_categories=request_categories,
                output_categories=output_categories,
                generated_text=generated_text,
                safe_output=safe_output,
                required_disclaimers=required_disclaimers,
                reason=reason,
            )

        if self._is_medical_adjacent(request_text) or self._is_medical_adjacent(generated_text):
            required_disclaimers = ("wellness_boundary", "clinician_consult")
            safe_output = self._append_disclaimers(generated_text, required_disclaimers)
            return self._result(
                status=SafetyStatus.rewritten,
                request_categories=(),
                output_categories=(),
                generated_text=generated_text,
                safe_output=safe_output,
                required_disclaimers=required_disclaimers,
                reason="disclaimer_injected",
            )

        return self._result(
            status=SafetyStatus.passed,
            request_categories=(),
            output_categories=(),
            generated_text=generated_text,
            safe_output=generated_text,
            required_disclaimers=(),
            reason="policy_passed",
        )

    def validate(
        self,
        output: LLMExplanationOutput,
        *,
        prompt_inputs: PromptInputs,
    ) -> dict[str, Any]:
        """SafetyGate-compatible post-generation validation for LLM orchestration."""

        result = self.evaluate(
            request_text=self._request_text(prompt_inputs),
            generated_text=self._llm_output_text(output),
        )
        payload = result.to_dict()
        if result.status is not SafetyStatus.passed:
            payload["safe_output"] = self._safe_llm_output(output, result).model_dump(mode="json")
        return payload

    def enforce_recommendation(
        self,
        recommendation: RecommendationContract,
    ) -> RecommendationContract:
        """Return a recommendation contract with authoritative safety fields applied."""

        result = self.evaluate(
            request_text=" ".join(recommendation.risk_flags),
            generated_text=_recommendation_text(recommendation),
        )
        if result.status is SafetyStatus.passed:
            return recommendation.model_copy(
                update={
                    "safety_status": result.status,
                    "safety_note": result.safety_note,
                    "safety_result": result.to_dict(),
                }
            )

        summary = RecommendationSummary(primary=result.safe_output, avoid=None)
        return recommendation.model_copy(
            update={
                "recommendation": summary,
                "safety_status": result.status,
                "safety_note": result.safety_note,
                "safety_result": result.to_dict(),
            }
        )

    def _classify(self, text: str) -> tuple[str, ...]:
        normalized = _normalize(text)
        matches: list[str] = []
        for category in self.categories:
            if any(_normalize(pattern) in normalized for pattern in category.trigger_patterns):
                matches.append(category.id)
        return tuple(matches)

    def _high_risk_output_categories(self, text: str) -> tuple[str, ...]:
        matches: list[str] = []
        for category_id, patterns in HIGH_RISK_OUTPUT_RULES:
            if any(pattern.search(text) for pattern in patterns):
                matches.append(category_id)
        return tuple(matches)

    def _status_for_categories(self, category_ids: Sequence[str]) -> SafetyStatus:
        if "emergency_medical_triage" in category_ids:
            return SafetyStatus.escalated
        return SafetyStatus.blocked

    def _required_disclaimers(self, category_ids: Sequence[str]) -> tuple[str, ...]:
        keys: list[str] = []
        by_id = {category.id: category for category in self.categories}
        for category_id in category_ids:
            category = by_id.get(category_id)
            if category is None:
                continue
            keys.extend(category.required_disclaimers)
        return _dedupe(tuple(keys))

    def _blocked_or_redirected_output(self, category_ids: Sequence[str]) -> str:
        by_id = {category.id: category for category in self.categories}
        parts: list[str] = []
        for category_id in category_ids:
            category = by_id.get(category_id)
            if category is None:
                continue
            parts.extend([category.safe_redirect, category.escalation])
        return self._append_disclaimers(
            " ".join(_dedupe(tuple(parts))),
            self._required_disclaimers(category_ids),
        )

    def _high_risk_rewrite(self, category_ids: Sequence[str]) -> str:
        if "emergency_medical_triage" in category_ids:
            return self._blocked_or_redirected_output(category_ids)
        return self._append_disclaimers(
            (
                "I need to keep this in wellness decision-support. The safe framing is "
                "non-diagnostic: summarize the signal, uncertainty, and lower-risk options "
                "or tradeoffs without diagnosis, treatment, dosing, or rehab instructions."
            ),
            self._required_disclaimers(category_ids) or ("wellness_boundary", "clinician_consult"),
        )

    def _append_disclaimers(self, text: str, disclaimer_keys: Sequence[str]) -> str:
        parts = [text.strip()]
        for key in disclaimer_keys:
            disclaimer = self.disclaimers.get(key)
            if disclaimer and disclaimer.lower() not in text.lower():
                parts.append(disclaimer)
        return " ".join(part for part in _dedupe(tuple(parts)) if part)

    def _is_medical_adjacent(self, text: str) -> bool:
        normalized = _normalize(text)
        return any(term in normalized for term in MEDICAL_ADJACENT_TERMS)

    def _result(
        self,
        *,
        status: SafetyStatus,
        request_categories: tuple[str, ...],
        output_categories: tuple[str, ...],
        generated_text: str,
        safe_output: str,
        required_disclaimers: tuple[str, ...],
        reason: str,
    ) -> SafetyResult:
        triggered = _dedupe(request_categories + output_categories)
        safety_note = self._append_disclaimers("", required_disclaimers).strip()
        if not safety_note:
            safety_note = self.disclaimers["wellness_boundary"]
        return SafetyResult(
            status=status,
            policy_version=self.policy_version,
            triggered_categories=triggered,
            request_categories=request_categories,
            output_categories=output_categories,
            required_disclaimers=required_disclaimers,
            original_output=generated_text,
            safe_output=safe_output,
            safety_note=safety_note,
            action=_action_for_status(status),
            reason=reason,
            evaluated_at=dt.datetime.now(dt.UTC),
        )

    def _request_text(self, prompt_inputs: PromptInputs) -> str:
        assessment = prompt_inputs.deterministic_assessment
        parts = [
            prompt_inputs.request_text or "",
            str(assessment.get("user_request", "")),
            str(assessment.get("request", "")),
            " ".join(str(item) for item in assessment.get("hard_safety_flags", [])),
            " ".join(prompt_inputs.raw_notes),
        ]
        return " ".join(part for part in parts if part)

    def _llm_output_text(self, output: LLMExplanationOutput) -> str:
        return " ".join(
            [
                output.summary,
                " ".join(output.rationale),
                " ".join(output.uncertainty),
            ]
        )

    def _safe_llm_output(
        self,
        output: LLMExplanationOutput,
        result: SafetyResult,
    ) -> LLMExplanationOutput:
        if result.triggered_categories:
            return output.model_copy(
                update={
                    "summary": result.safe_output,
                    "rationale": [
                        "The post-generation safety policy replaced unsafe medical content."
                    ],
                    "uncertainty": [
                        "Baseline can discuss wellness signals, not medical conclusions."
                    ],
                    "external_citations": [],
                    "safety_boundary_acknowledged": True,
                    "no_diagnosis_or_treatment_claims": True,
                }
            )
        return output.model_copy(update={"summary": result.safe_output})


def _category_from_payload(payload: Mapping[str, Any]) -> SafetyPolicyCategory:
    action = payload.get("action")
    if action not in {"refuse", "redirect"}:
        raise ValueError(f"Unsupported safety policy action: {action!r}")
    return SafetyPolicyCategory(
        id=str(payload["id"]),
        action=cast("CategoryAction", action),
        trigger_patterns=tuple(str(item) for item in _sequence(payload, "trigger_patterns")),
        safe_redirect=str(payload["safe_redirect"]),
        escalation=str(payload["escalation"]),
        required_disclaimers=tuple(
            str(item) for item in _sequence(payload, "required_disclaimers")
        ),
    )


def _recommendation_text(recommendation: RecommendationContract) -> str:
    parts = [
        recommendation.recommendation.primary,
        recommendation.recommendation.avoid or "",
        " ".join(recommendation.risk_flags),
        " ".join(recommendation.uncertainty),
    ]
    return " ".join(part for part in parts if part)


def _sequence(mapping: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Safety policy field {key!r} must be a list")
    return value


def _mapping(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Safety policy field {key!r} must be an object")
    return value


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _action_for_status(status: SafetyStatus) -> str:
    if status is SafetyStatus.escalated:
        return "escalate"
    if status is SafetyStatus.blocked:
        return "refuse"
    if status is SafetyStatus.rewritten:
        return "rewrite"
    return "allow"
