"""LLM provider interface and DeepSeek adapter."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(Exception):
    """Raised when a provider cannot complete a generation request."""


@dataclass(frozen=True)
class ProviderRequest:
    """Provider-neutral generation request."""

    model: str
    messages: list[dict[str, str]]
    response_schema: dict[str, Any]
    temperature: float = 0.2


@dataclass(frozen=True)
class ProviderResponse:
    """Provider-neutral generation response."""

    provider: str
    model: str
    content: str
    token_usage: dict[str, int] = field(default_factory=dict)
    cost: float | None = None
    latency_ms: int | None = None


class LLMProvider(Protocol):
    """Thin protocol implemented by concrete LLM providers."""

    name: str
    requires_external_llm_consent: bool

    async def generate(self, request: ProviderRequest) -> ProviderResponse: ...


class DeepSeekProvider:
    """DeepSeek chat-completions adapter.

    Tests should inject mocks or recorded providers. This class is intentionally
    small so no live network call is required by the orchestrator tests.
    """

    name = "deepseek"
    requires_external_llm_consent = True

    def __init__(
        self,
        *,
        api_key: str | None,
        endpoint: str = "https://api.deepseek.com/chat/completions",
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request: ProviderRequest) -> ProviderResponse:
        if not self._api_key:
            raise ProviderError("DeepSeek API key is not configured.")

        started = time.perf_counter()
        payload = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
        }
        encoded = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            self._endpoint,
            data=encoded,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"DeepSeek request failed: {exc}") from exc

        try:
            message = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("DeepSeek response did not include message content.") from exc

        usage = body.get("usage") or {}
        token_usage = {
            "prompt": int(usage.get("prompt_tokens", 0) or 0),
            "completion": int(usage.get("completion_tokens", 0) or 0),
            "total": int(usage.get("total_tokens", 0) or 0),
        }
        return ProviderResponse(
            provider=self.name,
            model=request.model,
            content=str(message),
            token_usage=token_usage,
            cost=None,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
