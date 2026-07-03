from fastapi import FastAPI

from baseline_api.api.health import router as health_router
from baseline_api.api.v1.contracts import router as v1_contracts_router
from baseline_api.api.v1.health import router as v1_health_router
from baseline_api.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(title=resolved_settings.app_name)
    app.state.settings = resolved_settings
    app.include_router(health_router)
    app.include_router(v1_health_router)
    app.include_router(v1_contracts_router)
    return app
