"""User resolution helpers for MVP single-user privacy controls."""

from __future__ import annotations

from sqlmodel import Session, col, select

from baseline_api.db.models.user import User
from baseline_api.privacy.errors import PrivacyError


def get_single_user(session: Session) -> User:
    users = list(session.exec(select(User).order_by(col(User.created_at)).limit(2)).all())
    if not users:
        raise PrivacyError(
            code="user_not_initialized",
            message="No Baseline user is available for privacy controls.",
            status_code=409,
        )
    if len(users) > 1:
        raise PrivacyError(
            code="ambiguous_user",
            message="Privacy controls require an authenticated user context.",
            status_code=409,
        )
    return users[0]
