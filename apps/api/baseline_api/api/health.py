from fastapi import APIRouter, Request

from baseline_api.config import Settings

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Application settings are not initialized.")
    return {"status": "ok", "service": settings.app_name}
