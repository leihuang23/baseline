"""Daily check-in API routes."""

from __future__ import annotations

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from baseline_api.checkin import (
    AnalysisJobQueue,
    ArqAnalysisJobQueue,
    CheckinError,
    CheckinService,
    NoteRedactionService,
    StubNoteRedactionService,
)
from baseline_api.config import Settings
from baseline_api.db.session import get_db_session
from baseline_api.schemas.api import (
    DailyCheckInDetailResponse,
    DailyCheckInRequest,
    DailyCheckInResponse,
)
from baseline_api.schemas.common import APIEnvelope, APIError

router = APIRouter(prefix="/v1/checkins", tags=["checkins"])


def get_redaction_service(request: Request) -> NoteRedactionService:
    """Return the configured note redaction service or the privacy-safe stub."""

    service = getattr(request.app.state, "redaction_service", None)
    if service is not None:
        return service  # type: ignore[no-any-return]
    return StubNoteRedactionService()


def get_analysis_queue(request: Request) -> AnalysisJobQueue:
    """Return the configured analysis job queue or the Redis-backed default."""

    queue = getattr(request.app.state, "analysis_queue", None)
    if queue is not None:
        return queue  # type: ignore[no-any-return]

    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Application settings are not initialized.")
    return ArqAnalysisJobQueue(str(settings.redis_url))


@router.post("/daily", response_model=APIEnvelope[DailyCheckInResponse])
async def submit_daily_checkin(
    request: DailyCheckInRequest,
    session: Annotated[Session, Depends(get_db_session)],
    redaction: Annotated[NoteRedactionService, Depends(get_redaction_service)],
    queue: Annotated[AnalysisJobQueue, Depends(get_analysis_queue)],
) -> APIEnvelope[DailyCheckInResponse] | JSONResponse:
    service = CheckinService(session, redaction, queue)
    try:
        data = await service.create_checkin(request)
    except CheckinError as error:
        return _error_response(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
        )
    return APIEnvelope(status="success", data=data)


@router.put("/daily/{checkin_id}", response_model=APIEnvelope[DailyCheckInResponse])
async def update_daily_checkin(
    checkin_id: UUID,
    request: DailyCheckInRequest,
    session: Annotated[Session, Depends(get_db_session)],
    redaction: Annotated[NoteRedactionService, Depends(get_redaction_service)],
    queue: Annotated[AnalysisJobQueue, Depends(get_analysis_queue)],
) -> APIEnvelope[DailyCheckInResponse] | JSONResponse:
    service = CheckinService(session, redaction, queue)
    try:
        data = await service.update_checkin(checkin_id, request)
    except CheckinError as error:
        return _error_response(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
        )
    return APIEnvelope(status="success", data=data)


@router.get("/daily/by-date/{checkin_date}", response_model=APIEnvelope[DailyCheckInDetailResponse])
def get_daily_checkin(
    checkin_date: dt.date,
    session: Annotated[Session, Depends(get_db_session)],
    redaction: Annotated[NoteRedactionService, Depends(get_redaction_service)],
    queue: Annotated[AnalysisJobQueue, Depends(get_analysis_queue)],
) -> APIEnvelope[DailyCheckInDetailResponse] | JSONResponse:
    service = CheckinService(session, redaction, queue)
    try:
        data = service.get_checkin_for_date(checkin_date)
    except CheckinError as error:
        return _error_response(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
        )
    return APIEnvelope(status="success", data=data)


@router.delete(
    "/daily/{checkin_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_daily_checkin(
    checkin_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    redaction: Annotated[NoteRedactionService, Depends(get_redaction_service)],
    queue: Annotated[AnalysisJobQueue, Depends(get_analysis_queue)],
) -> None | JSONResponse:
    service = CheckinService(session, redaction, queue)
    try:
        await service.delete_checkin(checkin_id)
    except CheckinError as error:
        return _error_response(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
        )
    return None


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
