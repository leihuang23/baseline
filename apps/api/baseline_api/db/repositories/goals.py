"""Goal repository."""

from uuid import UUID

from sqlmodel import Session, col, select

from baseline_api.db.models.goals import Goal
from baseline_api.db.repositories.base import BaseRepository


class GoalRepository(BaseRepository[Goal]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Goal)

    def get_for_user(self, goal_id: UUID, user_id: UUID) -> Goal | None:
        goal = self.get_by_id(goal_id)
        if goal is None or goal.user_id != user_id:
            return None
        return goal

    def list_for_user(self, user_id: UUID) -> list[Goal]:
        return list(
            self.session.exec(
                select(Goal)
                .where(Goal.user_id == user_id)
                .order_by(
                    col(Goal.active).desc(),
                    col(Goal.priority).desc(),
                    col(Goal.created_at).desc(),
                )
            ).all()
        )

    def list_active_for_user(self, user_id: UUID) -> list[Goal]:
        return list(
            self.session.exec(
                select(Goal)
                .where(Goal.user_id == user_id, Goal.active)
                .order_by(col(Goal.priority).desc(), col(Goal.created_at).asc())
            ).all()
        )
