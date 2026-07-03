from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from baseline_api.api.health import router as health_router
from baseline_api.api.v1.contracts import router as v1_contracts_router
from baseline_api.api.v1.health import router as v1_health_router
from baseline_api.config import Settings, get_settings
from baseline_api.observability import configure_logging, metrics_router, trace_id_middleware
from baseline_api.schemas.common import APIEnvelope, APIError


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    app = FastAPI(title=resolved_settings.app_name)
    app.state.settings = resolved_settings
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.middleware("http")(trace_id_middleware)
    app.include_router(health_router)
    app.include_router(v1_health_router)
    app.include_router(v1_contracts_router)
    app.include_router(metrics_router)
    return app


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
