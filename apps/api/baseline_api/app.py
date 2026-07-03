from fastapi import FastAPI

from baseline_api.api.health import router as health_router
from baseline_api.api.v1.contracts import router as v1_contracts_router
from baseline_api.api.v1.health import router as v1_health_router
from baseline_api.config import Settings, get_settings
from baseline_api.observability import configure_logging, metrics_router, trace_id_middleware


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    app = FastAPI(title=resolved_settings.app_name)
    app.state.settings = resolved_settings
    app.middleware("http")(trace_id_middleware)
    app.include_router(health_router)
    app.include_router(v1_health_router)
    app.include_router(v1_contracts_router)
    app.include_router(metrics_router)
    return app
