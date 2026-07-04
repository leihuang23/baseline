"""Assistant Q&A API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session

from baseline_api.assistant import AssistantQueryError, AssistantQueryService
from baseline_api.db.session import get_db_session
from baseline_api.schemas.api import AssistantQueryRequest, AssistantQueryResponse
from baseline_api.schemas.common import APIEnvelope, APIError

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])


@router.post("/query", response_model=APIEnvelope[AssistantQueryResponse])
def ask_assistant(
    request: AssistantQueryRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> APIEnvelope[AssistantQueryResponse] | JSONResponse:
    service = AssistantQueryService(session)
    try:
        data = service.answer(request)
    except AssistantQueryError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


def _error_response(error: AssistantQueryError) -> JSONResponse:
    envelope: APIEnvelope[None] = APIEnvelope(
        status="error",
        error=APIError(code=error.code, message=error.message, details=error.details),
    )
    return JSONResponse(
        status_code=error.status_code,
        content=envelope.model_dump(mode="json"),
    )
