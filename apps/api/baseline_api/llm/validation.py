"""Structured-output parsing and graceful degradation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from baseline_api.llm.schemas import LLMExplanationOutput


class StructuredOutputError(Exception):
    """Raised when provider output is not valid JSON for the requested schema."""


def parse_structured_output(content: str) -> LLMExplanationOutput:
    """Parse a provider response into the Pydantic output schema."""

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"Response was not valid JSON: {exc.msg}") from exc

    try:
        return LLMExplanationOutput.model_validate(payload)
    except ValidationError as exc:
        raise StructuredOutputError(str(exc)) from exc


def degraded_output(
    *,
    deterministic_assessment: dict[str, Any],
    reason: str,
) -> LLMExplanationOutput:
    """Produce a schema-valid deterministic fallback without LLM-authored advice."""

    readiness = str(deterministic_assessment.get("readiness_state", "unknown"))
    band = str(deterministic_assessment.get("recommendation_band", "unknown"))
    uncertainty = deterministic_assessment.get("uncertainty")
    if not isinstance(uncertainty, list) or not uncertainty:
        uncertainty = [
            "LLM explanation was unavailable, so uncertainty is carried from rules only."
        ]

    evidence_items = deterministic_assessment.get("evidence_items")
    evidence_refs = _evidence_refs(evidence_items)

    return LLMExplanationOutput(
        summary=(
            "LLM explanation unavailable; using the deterministic assessment without "
            "additional prose recommendations."
        ),
        rationale=[
            f"Deterministic readiness_state={readiness}.",
            f"Deterministic recommendation_band={band}.",
            f"Degrade reason: {reason}.",
        ],
        uncertainty=[str(item) for item in uncertainty],
        personal_evidence_refs=evidence_refs,
        external_citations=[],
        safety_boundary_acknowledged=True,
        no_diagnosis_or_treatment_claims=True,
    )


def _evidence_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return ["deterministic_assessment"]

    refs: list[str] = []
    for item in value:
        if isinstance(item, dict):
            ref = item.get("source") or item.get("metric")
            if ref:
                refs.append(str(ref))
    return refs or ["deterministic_assessment"]
