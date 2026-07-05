"""Model routing and provider fallback selection."""

from __future__ import annotations

from dataclasses import dataclass

from baseline_api.llm.providers import LLMProvider
from baseline_api.llm.schemas import TaskType


@dataclass(frozen=True)
class ModelRoute:
    provider_name: str
    model: str


class ModelRouter:
    """Route simple tasks to cheap models and complex tasks to strong models."""

    def __init__(
        self,
        *,
        providers: list[LLMProvider],
        cheap_model: str,
        strong_model: str,
        provider_model_overrides: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("at least one LLM provider is required")
        self._providers = {provider.name: provider for provider in providers}
        self._provider_order = [provider.name for provider in providers]
        self._cheap_model = cheap_model
        self._strong_model = strong_model
        self._provider_model_overrides = provider_model_overrides or {}

    def routes_for(self, task_type: TaskType) -> list[ModelRoute]:
        default_model = self._strong_model if task_type in _STRONG_TASKS else self._cheap_model
        model_index = 1 if task_type in _STRONG_TASKS else 0
        return [
            ModelRoute(
                provider_name=name,
                model=self._provider_model_overrides.get(name, (default_model, default_model))[
                    model_index
                ],
            )
            for name in self._provider_order
        ]

    def provider(self, name: str) -> LLMProvider:
        return self._providers[name]


_STRONG_TASKS = {
    TaskType.complex_longitudinal,
    TaskType.planning,
}
