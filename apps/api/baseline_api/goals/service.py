"""Goal-management service and active goal-set accessor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from baseline_api.db.models.enums import GoalCategory as ModelGoalCategory
from baseline_api.db.models.enums import TimeHorizon as ModelTimeHorizon
from baseline_api.db.models.goals import Goal
from baseline_api.db.models.user import User
from baseline_api.db.repositories.goals import GoalRepository
from baseline_api.privacy.user import resolve_single_user
from baseline_api.schemas.api import ActiveGoal, ActiveGoalSet, GoalRequest, GoalResponse
from baseline_api.schemas.enums import GoalCategory, GoalTimeHorizon


@dataclass(frozen=True)
class GoalError(Exception):
    """Domain error raised by the goal service."""

    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None


class GoalService:
    """Create and manage user goals used by the reasoning engine."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._goals = GoalRepository(session)

    def list_goals(self, *, user: User | None = None) -> list[GoalResponse]:
        resolved_user = self._resolve_user(user)
        return [self._to_response(goal) for goal in self._goals.list_for_user(resolved_user.id)]

    def get_goal(self, goal_id: UUID, *, user: User | None = None) -> GoalResponse:
        resolved_user = self._resolve_user(user)
        goal = self._get_goal_for_user(goal_id, resolved_user.id)
        return self._to_response(goal)

    def create_goal(self, request: GoalRequest, *, user: User | None = None) -> GoalResponse:
        resolved_user = self._resolve_user(user)
        goal = Goal(
            user_id=resolved_user.id,
            category=ModelGoalCategory(request.category.value),
            priority=request.priority,
            time_horizon=ModelTimeHorizon(request.time_horizon.value),
            success_metric=request.success_metric,
            constraints=dict(request.constraints),
            active=True,
        )
        self._goals.create(goal)
        self._session.commit()
        self._session.refresh(goal)
        return self._to_response(goal)

    def update_goal(
        self,
        goal_id: UUID,
        request: GoalRequest,
        *,
        user: User | None = None,
    ) -> GoalResponse:
        resolved_user = self._resolve_user(user)
        goal = self._get_goal_for_user(goal_id, resolved_user.id)
        goal.category = ModelGoalCategory(request.category.value)
        goal.priority = request.priority
        goal.time_horizon = ModelTimeHorizon(request.time_horizon.value)
        goal.success_metric = request.success_metric
        goal.constraints = dict(request.constraints)
        goal.updated_at = datetime.now(UTC)
        self._session.add(goal)
        self._session.commit()
        self._session.refresh(goal)
        return self._to_response(goal)

    def delete_goal(self, goal_id: UUID, *, user: User | None = None) -> None:
        resolved_user = self._resolve_user(user)
        goal = self._get_goal_for_user(goal_id, resolved_user.id)
        self._session.delete(goal)
        self._session.commit()

    def pause_goal(self, goal_id: UUID, *, user: User | None = None) -> GoalResponse:
        resolved_user = self._resolve_user(user)
        goal = self._get_goal_for_user(goal_id, resolved_user.id)
        goal.active = False
        goal.paused_at = datetime.now(UTC)
        goal.updated_at = datetime.now(UTC)
        self._session.add(goal)
        self._session.commit()
        self._session.refresh(goal)
        return self._to_response(goal)

    def resume_goal(self, goal_id: UUID, *, user: User | None = None) -> GoalResponse:
        resolved_user = self._resolve_user(user)
        goal = self._get_goal_for_user(goal_id, resolved_user.id)
        goal.active = True
        goal.paused_at = None
        goal.updated_at = datetime.now(UTC)
        self._session.add(goal)
        self._session.commit()
        self._session.refresh(goal)
        return self._to_response(goal)

    def get_active_goal_set(self, *, user: User | None = None) -> ActiveGoalSet:
        resolved_user = self._resolve_user(user)
        active_goals = self._goals.list_active_for_user(resolved_user.id)
        goals = [
            ActiveGoal(
                goal_id=goal.id,
                priority_order=index,
                category=GoalCategory(goal.category.value),
                priority=goal.priority,
                time_horizon=GoalTimeHorizon(goal.time_horizon.value),
                success_metric=goal.success_metric,
                constraints=self._string_constraints(goal.constraints),
            )
            for index, goal in enumerate(active_goals, start=1)
        ]
        return ActiveGoalSet(
            user_id=resolved_user.id,
            goals=goals,
            category_priorities=self._category_priorities(goals),
            horizons_by_category=self._horizons_by_category(goals),
            constraints_by_category=self._constraints_by_category(goals),
        )

    def _resolve_user(self, user: User | None = None) -> User:
        if user is not None:
            return user
        return resolve_single_user(
            self._session,
            empty_error_factory=lambda: GoalError(
                code="user_not_initialized",
                message="No Baseline user is available for goals.",
                status_code=409,
            ),
            ambiguous_error_factory=lambda: GoalError(
                code="ambiguous_user",
                message="Goals require an authenticated user context.",
                status_code=409,
            ),
        )

    def _get_goal_for_user(self, goal_id: UUID, user_id: UUID) -> Goal:
        goal = self._goals.get_for_user(goal_id, user_id)
        if goal is None:
            raise GoalError(
                code="goal_not_found",
                message="Goal not found.",
                status_code=404,
            )
        return goal

    @classmethod
    def _to_response(cls, goal: Goal) -> GoalResponse:
        return GoalResponse(
            id=goal.id,
            category=GoalCategory(goal.category.value),
            priority=goal.priority,
            time_horizon=GoalTimeHorizon(goal.time_horizon.value),
            success_metric=goal.success_metric,
            constraints=cls._string_constraints(goal.constraints),
            active=goal.active,
        )

    @staticmethod
    def _string_constraints(constraints: dict[str, Any]) -> dict[str, str]:
        return {str(key): str(value) for key, value in constraints.items()}

    @staticmethod
    def _category_priorities(goals: list[ActiveGoal]) -> dict[str, int]:
        priorities: dict[str, int] = {}
        for goal in goals:
            current = priorities.get(goal.category.value)
            if current is None or goal.priority > current:
                priorities[goal.category.value] = goal.priority
        return priorities

    @staticmethod
    def _horizons_by_category(goals: list[ActiveGoal]) -> dict[str, list[GoalTimeHorizon]]:
        horizons: dict[str, list[GoalTimeHorizon]] = {}
        for goal in goals:
            category_horizons = horizons.setdefault(goal.category.value, [])
            if goal.time_horizon not in category_horizons:
                category_horizons.append(goal.time_horizon)
        return horizons

    @staticmethod
    def _constraints_by_category(goals: list[ActiveGoal]) -> dict[str, list[dict[str, str]]]:
        constraints: dict[str, list[dict[str, str]]] = {}
        for goal in goals:
            if goal.constraints:
                constraints.setdefault(goal.category.value, []).append(goal.constraints)
        return constraints
