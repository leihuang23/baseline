"""Goal-management API routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, col, select

from baseline_api.db.models.enums import GoalCategory as ModelGoalCategory
from baseline_api.db.models.enums import TimeHorizon as ModelTimeHorizon
from baseline_api.db.models.goals import Goal
from baseline_api.db.models.user import User
from baseline_api.db.session import get_db_session
from baseline_api.schemas.api import GoalRequest, GoalResponse
from baseline_api.schemas.common import APIEnvelope, APIError
from baseline_api.schemas.enums import GoalCategory, GoalTimeHorizon

router = APIRouter(prefix="/v1/goals", tags=["goals"])


@dataclass(frozen=True)
class GoalAPIError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None


@router.get("", response_model=APIEnvelope[list[GoalResponse]])
def list_goals(
    session: Annotated[Session, Depends(get_db_session)],
) -> APIEnvelope[list[GoalResponse]] | JSONResponse:
    try:
        user = _get_single_user(session)
    except GoalAPIError as error:
        return _error_response(error)

    goals = session.exec(
        select(Goal)
        .where(Goal.user_id == user.id)
        .order_by(col(Goal.active).desc(), col(Goal.priority).desc(), col(Goal.created_at).desc())
    ).all()
    return APIEnvelope(status="success", data=[_to_response(goal) for goal in goals])


@router.post("", response_model=APIEnvelope[GoalResponse])
def create_goal(
    request: GoalRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> APIEnvelope[GoalResponse] | JSONResponse:
    try:
        user = _get_single_user(session)
    except GoalAPIError as error:
        return _error_response(error)

    goal = Goal(
        user_id=user.id,
        category=ModelGoalCategory(request.category.value),
        priority=request.priority,
        time_horizon=ModelTimeHorizon(request.time_horizon.value),
        success_metric=request.success_metric,
        constraints=request.constraints,
        active=True,
    )
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return APIEnvelope(status="success", data=_to_response(goal))


@router.post("/{goal_id}/pause", response_model=APIEnvelope[GoalResponse])
def pause_goal(
    goal_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> APIEnvelope[GoalResponse] | JSONResponse:
    try:
        user = _get_single_user(session)
        goal = session.get(Goal, goal_id)
        if goal is None or goal.user_id != user.id:
            raise GoalAPIError(
                code="goal_not_found",
                message="Goal not found.",
                status_code=404,
            )
    except GoalAPIError as error:
        return _error_response(error)

    goal.active = False
    goal.paused_at = datetime.now(UTC)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return APIEnvelope(status="success", data=_to_response(goal))


def _get_single_user(session: Session) -> User:
    users = list(session.exec(select(User).order_by(col(User.created_at)).limit(2)).all())
    if not users:
        raise GoalAPIError(
            code="user_not_initialized",
            message="No Baseline user is available for goals.",
            status_code=409,
        )
    if len(users) > 1:
        raise GoalAPIError(
            code="ambiguous_user",
            message="Goals require an authenticated user context.",
            status_code=409,
        )
    return users[0]


def _to_response(goal: Goal) -> GoalResponse:
    return GoalResponse(
        id=goal.id,
        category=GoalCategory(goal.category.value),
        priority=goal.priority,
        time_horizon=GoalTimeHorizon(goal.time_horizon.value),
        success_metric=goal.success_metric,
        constraints={str(key): str(value) for key, value in goal.constraints.items()},
        active=goal.active,
    )


def _error_response(error: GoalAPIError) -> JSONResponse:
    envelope: APIEnvelope[None] = APIEnvelope(
        status="error",
        error=APIError(code=error.code, message=error.message, details=error.details),
    )
    return JSONResponse(
        status_code=error.status_code,
        content=envelope.model_dump(mode="json"),
    )
