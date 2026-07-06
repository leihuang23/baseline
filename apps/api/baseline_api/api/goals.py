"""Goal-management API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from baseline_api.api.deps import SingleUserContext, get_single_user_context
from baseline_api.db.session import get_db_session
from baseline_api.goals import GoalError, GoalService
from baseline_api.privacy import PrivacyError
from baseline_api.schemas.api import GoalRequest, GoalResponse
from baseline_api.schemas.common import APIEnvelope, APIError

router = APIRouter(prefix="/v1/goals", tags=["goals"])


@router.get("", response_model=APIEnvelope[list[GoalResponse]])
def list_goals(
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[list[GoalResponse]] | JSONResponse:
    service = GoalService(session)
    try:
        data = service.list_goals(user=context.user)
    except GoalError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/{goal_id}", response_model=APIEnvelope[GoalResponse])
def get_goal(
    goal_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[GoalResponse] | JSONResponse:
    service = GoalService(session)
    try:
        data = service.get_goal(goal_id, user=context.user)
    except GoalError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.post("", response_model=APIEnvelope[GoalResponse])
def create_goal(
    request: GoalRequest,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[GoalResponse] | JSONResponse:
    service = GoalService(session)
    try:
        data = service.create_goal(request, user=context.user)
    except GoalError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.put("/{goal_id}", response_model=APIEnvelope[GoalResponse])
def update_goal(
    goal_id: UUID,
    request: GoalRequest,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[GoalResponse] | JSONResponse:
    service = GoalService(session)
    try:
        data = service.update_goal(goal_id, request, user=context.user)
    except GoalError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.delete(
    "/{goal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_goal(
    goal_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> None | JSONResponse:
    service = GoalService(session)
    try:
        service.delete_goal(goal_id, user=context.user)
    except GoalError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return None


@router.post("/{goal_id}/pause", response_model=APIEnvelope[GoalResponse])
def pause_goal(
    goal_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[GoalResponse] | JSONResponse:
    service = GoalService(session)
    try:
        data = service.pause_goal(goal_id, user=context.user)
    except GoalError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.post("/{goal_id}/resume", response_model=APIEnvelope[GoalResponse])
def resume_goal(
    goal_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[GoalResponse] | JSONResponse:
    service = GoalService(session)
    try:
        data = service.resume_goal(goal_id, user=context.user)
    except GoalError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


def _error_response(error: GoalError | PrivacyError) -> JSONResponse:
    envelope: APIEnvelope[None] = APIEnvelope(
        status="error",
        error=APIError(code=error.code, message=error.message, details=error.details),
    )
    return JSONResponse(
        status_code=error.status_code,
        content=envelope.model_dump(mode="json"),
    )
