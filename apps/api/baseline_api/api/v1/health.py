from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from baseline_api.config import Settings
from baseline_api.db.models.ingestion import HealthImportBatch
from baseline_api.db.session import get_db_session
from baseline_api.ingestion import (
    ArqNormalizationJobQueue,
    HealthSyncService,
    IngestionError,
    NormalizationJobQueue,
)
from baseline_api.schemas.api import HealthSyncRequest, HealthSyncResponse
from baseline_api.schemas.common import APIEnvelope, APIError

router = APIRouter(prefix="/v1/health", tags=["health"])

HEALTH_SYNC_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {
        "model": APIEnvelope[None],
        "description": "Consent is missing, revoked, invalid, or does not allow a sample category.",
    },
    status.HTTP_409_CONFLICT: {
        "model": APIEnvelope[None],
        "description": "The sync request conflicts with idempotency or ingestion state.",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": APIEnvelope[None],
        "description": "Raw samples were persisted, but normalization could not be queued.",
    },
}


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "ok"}


def get_normalization_queue(request: Request) -> NormalizationJobQueue:
    queue = getattr(request.app.state, "normalization_queue", None)
    if queue is not None:
        return cast(NormalizationJobQueue, queue)

    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Application settings are not initialized.")
    return ArqNormalizationJobQueue(str(settings.redis_url))


@router.post(
    "/sync",
    response_model=APIEnvelope[HealthSyncResponse],
    responses=HEALTH_SYNC_ERROR_RESPONSES,
)
async def sync_health(
    request: HealthSyncRequest,
    session: Annotated[Session, Depends(get_db_session)],
    queue: Annotated[NormalizationJobQueue, Depends(get_normalization_queue)],
) -> APIEnvelope[HealthSyncResponse] | JSONResponse:
    service = HealthSyncService(session)
    try:
        result = service.sync(request)
        session.commit()
    except IngestionError as error:
        return _error_response(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    pending = result.pending_normalization
    if pending is not None:
        try:
            job_id = await queue.enqueue_batch(
                import_batch_id=pending.import_batch_id,
                user_id=pending.user_id,
            )
        except Exception:
            session.rollback()
            return _error_response(
                status_code=503,
                code="normalization_enqueue_failed",
                message="Health sync was persisted, but normalization could not be queued.",
            )
        if job_id is not None:
            batch = session.get(HealthImportBatch, pending.import_batch_id)
            if batch is not None:
                batch.normalization_job_id = job_id
                session.add(batch)
                session.commit()

    return APIEnvelope(status="success", data=result.response)


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    envelope: APIEnvelope[None] = APIEnvelope(
        status="error",
        error=APIError(code=code, message=message, details=details),
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
    )
