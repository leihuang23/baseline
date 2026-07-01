from fastapi import APIRouter

router = APIRouter(prefix="/v1/health", tags=["health"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "ok"}
