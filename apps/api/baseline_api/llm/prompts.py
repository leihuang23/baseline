"""Versioned prompt registry for Baseline LLM explanations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from baseline_api.llm.hash import canonical_json
from baseline_api.llm.schemas import PromptInputs

PROMPT_VERSION = "p3-04-explanation-v1"

SAFETY_BOUNDARY = (
    "Baseline is wellness decision support for training and recovery. It is not a medical "
    "tool, does not diagnose conditions, does not prescribe treatment, and must not override "
    "deterministic safety flags."
)

PROMPT_REQUIREMENTS = (
    "Use only the provided structured feature assessment, derived features, and retrieved "
    "evidence. Do not fabricate metrics, trends, citations, or missing context. State explicit "
    "uncertainty. Forbid diagnosis and treatment claims. Cite every external-knowledge claim "
    "using the provided external knowledge objects. Return only schema-valid JSON."
)

MINIMIZED_FEATURE_DENYLIST = {
    "raw_samples",
    "raw_health_samples",
    "source_sample_ids",
    "free_text_note",
    "raw_notes",
}


@dataclass(frozen=True)
class PromptMessages:
    version: str
    messages: list[dict[str, str]]


class PromptRegistry:
    """Render versioned prompts with the required safety and schema boundary."""

    def render(self, inputs: PromptInputs, response_schema: dict[str, Any]) -> PromptMessages:
        minimized = self._minimize(inputs)
        user_payload = {
            "task_type": inputs.task_type.value,
            "structured_feature_assessment": minimized["deterministic_assessment"],
            "derived_features": minimized["derived_features"],
            "retrieved_evidence_only": minimized["retrieved_evidence"],
            "external_knowledge": minimized["external_knowledge"],
            "output_json_schema": response_schema,
        }
        return PromptMessages(
            version=PROMPT_VERSION,
            messages=[
                {
                    "role": "system",
                    "content": f"{SAFETY_BOUNDARY}\n\n{PROMPT_REQUIREMENTS}",
                },
                {
                    "role": "user",
                    "content": canonical_json(user_payload),
                },
            ],
        )

    def repair(
        self,
        *,
        original: PromptMessages,
        invalid_output: str,
        validation_error: str,
    ) -> PromptMessages:
        repair_messages = [
            *original.messages,
            {
                "role": "assistant",
                "content": invalid_output,
            },
            {
                "role": "user",
                "content": (
                    "The previous response failed schema validation. Return corrected JSON only. "
                    f"Validation error: {validation_error}"
                ),
            },
        ]
        return PromptMessages(version=original.version, messages=repair_messages)

    def _minimize(self, inputs: PromptInputs) -> dict[str, Any]:
        payload = inputs.model_dump(mode="json")
        payload.pop("raw_samples", None)
        payload.pop("raw_notes", None)
        return cast(dict[str, Any], self._strip_denied_keys(payload))

    def _strip_denied_keys(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._strip_denied_keys(item)
                for key, item in value.items()
                if key not in MINIMIZED_FEATURE_DENYLIST
            }
        if isinstance(value, list):
            return [self._strip_denied_keys(item) for item in value]
        return value
