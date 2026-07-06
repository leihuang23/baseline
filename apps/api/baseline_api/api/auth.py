"""Request authentication for deployable API surfaces."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from hmac import compare_digest
from typing import Final

from fastapi import Request
from starlette.responses import JSONResponse, Response

from baseline_api.config import Settings

_HEALTH_EXACT_PATHS: Final[frozenset[str]] = frozenset({"/health", "/v1/health/ping"})
_DOCS_EXACT_PATHS: Final[frozenset[str]] = frozenset({"/docs", "/redoc", "/openapi.json"})
_DOCS_PREFIXES: Final[tuple[str, ...]] = ("/docs/", "/static/")


def is_public_api_path(path: str, app_env: str | None = None) -> bool:
    """Return True for health checks; docs are public only in local/test."""

    if path in _HEALTH_EXACT_PATHS:
        return True
    if app_env in {"staging", "production"}:
        return False
    return path in _DOCS_EXACT_PATHS or path.startswith(_DOCS_PREFIXES)


async def api_key_auth_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Require a configured bearer/API-key token outside public health/docs paths."""

    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings) or not settings.api_auth_token:
        return await call_next(request)

    path = request.url.path
    if is_public_api_path(path, app_env=settings.app_env):
        return await call_next(request)

    if not _request_has_valid_token(request, settings.api_auth_token):
        return JSONResponse(
            status_code=401,
            content={
                "status": "error",
                "error": {
                    "code": "authentication_required",
                    "message": "A valid Baseline API token is required.",
                },
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)


def _request_has_valid_token(request: Request, expected: str) -> bool:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return compare_digest(token, expected)

    api_key = request.headers.get("x-baseline-api-key")
    return api_key is not None and compare_digest(api_key, expected)
