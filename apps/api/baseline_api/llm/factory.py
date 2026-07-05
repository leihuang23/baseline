"""Factory helpers for application wiring."""

from __future__ import annotations

from baseline_api.config import Settings
from baseline_api.llm.providers import DeepSeekProvider, LocalDeterministicFallbackProvider
from baseline_api.llm.router import ModelRouter


def build_default_router(settings: Settings) -> ModelRouter:
    """Build the configured provider router.

    DeepSeek is the default provider for P3-04. Additional providers can be
    appended here without changing orchestration behavior.
    """

    provider = DeepSeekProvider(
        api_key=settings.deepseek_api_key,
        endpoint=settings.deepseek_api_url,
    )
    fallback = LocalDeterministicFallbackProvider()
    return ModelRouter(
        providers=[provider, fallback],
        cheap_model=settings.llm_cheap_model,
        strong_model=settings.llm_strong_model,
        provider_model_overrides={
            fallback.name: (settings.llm_fallback_model, settings.llm_fallback_model)
        },
    )
