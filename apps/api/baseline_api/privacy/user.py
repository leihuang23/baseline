"""User resolution helpers for MVP single-user privacy controls."""

from __future__ import annotations

from collections.abc import Callable

from sqlmodel import Session, col, select

from baseline_api.db.models.user import User
from baseline_api.privacy.errors import PrivacyError


def list_single_user_candidates(session: Session) -> list[User]:
    """Return up to two users so callers can distinguish none, one, or ambiguous."""

    users = list(session.exec(select(User).order_by(col(User.created_at)).limit(2)).all())
    return users


def get_single_user(session: Session) -> User:
    users = list_single_user_candidates(session)
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


def resolve_single_user(
    session: Session,
    *,
    empty_error_factory: Callable[[], Exception] | None = None,
    ambiguous_error_factory: Callable[[], Exception] | None = None,
) -> User:
    """Resolve the private-deployment user while preserving caller-specific errors."""

    users = list_single_user_candidates(session)
    if not users:
        if empty_error_factory is not None:
            raise empty_error_factory()
        raise PrivacyError(
            code="user_not_initialized",
            message="No Baseline user is available for privacy controls.",
            status_code=409,
        )
    if len(users) > 1:
        if ambiguous_error_factory is not None:
            raise ambiguous_error_factory()
        raise PrivacyError(
            code="ambiguous_user",
            message="Privacy controls require an authenticated user context.",
            status_code=409,
        )
    return users[0]
