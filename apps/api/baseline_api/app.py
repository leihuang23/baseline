from typing import Any, cast

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from baseline_api.api.assistant import router as assistant_router
from baseline_api.api.auth import api_key_auth_middleware, is_public_api_path
from baseline_api.api.checkins import router as checkins_router
from baseline_api.api.data import _cleanup_expired_exports
from baseline_api.api.data import router as data_router
from baseline_api.api.goals import router as goals_router
from baseline_api.api.health import router as health_router
from baseline_api.api.v1.contracts import router as v1_contracts_router
from baseline_api.api.v1.health import router as v1_health_router
from baseline_api.api.v1.observability import router as v1_observability_router
from baseline_api.config import Settings, get_settings
from baseline_api.observability import configure_logging, metrics_router, trace_id_middleware
from baseline_api.observability.logging import log_event
from baseline_api.privacy import LocalExportStore
from baseline_api.schemas.common import APIEnvelope, APIError


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    app = FastAPI(title=resolved_settings.app_name)
    app.state.settings = resolved_settings

    if resolved_settings.export_cleanup_on_start:
        @app.on_event("startup")
        async def cleanup_export_store() -> None:
            try:
                store = LocalExportStore(
                    resolved_settings.export_storage_dir,
                    retention_hours=resolved_settings.export_retention_hours,
                )
            except Exception as exc:
                log_event(
                    "data_export_store_startup",
                    status="failed",
                    level="warning",
                    error_class=type(exc).__name__,
                )
                return
            app.state.export_store = store
            await _cleanup_expired_exports(store)

    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.middleware("http")(api_key_auth_middleware)
    app.middleware("http")(trace_id_middleware)
    app.include_router(health_router)
    app.include_router(v1_health_router)
    app.include_router(v1_observability_router)
    app.include_router(assistant_router)
    app.include_router(v1_contracts_router)
    app.include_router(checkins_router)
    app.include_router(data_router)
    app.include_router(goals_router)
    app.include_router(metrics_router)
    _install_openapi_auth_contract(app)
    return app


def _install_openapi_auth_contract(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
            description=app.description,
            summary=app.summary,
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BaselineBearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Baseline API token from BASELINE_API_AUTH_TOKEN.",
        }
        security_schemes["BaselineApiKeyAuth"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-Baseline-API-Key",
            "description": "Baseline API token from BASELINE_API_AUTH_TOKEN.",
        }

        security: list[dict[str, list[str]]] = [
            {"BaselineBearerAuth": []},
            {"BaselineApiKeyAuth": []},
        ]
        for path, operations in schema.get("paths", {}).items():
            if is_public_api_path(path):
                continue
            for method, operation in operations.items():
                if method not in {"get", "put", "post", "delete", "patch", "options", "head"}:
                    continue
                operation.setdefault("security", security)
                operation.setdefault("responses", {}).setdefault(
                    "401",
                    {
                        "description": "Authentication required.",
                    },
                )

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


async def _validation_exception_handler(
    request: Request,
    exc: Exception,
) -> Response:
    validation_error = cast(RequestValidationError, exc)
    if request.url.path == "/v1/health/sync" and _body_field_has_error(
        validation_error,
        "consent_version",
    ):
        envelope: APIEnvelope[None] = APIEnvelope(
            status="error",
            error=APIError(
                code="consent_invalid",
                message="Consent version is not active for health ingestion.",
            ),
        )
        return JSONResponse(status_code=403, content=envelope.model_dump(mode="json"))

    return await request_validation_exception_handler(request, validation_error)


def _body_field_has_error(exc: RequestValidationError, field_name: str) -> bool:
    return any(tuple(error.get("loc", ())) == ("body", field_name) for error in exc.errors())
