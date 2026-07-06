"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from baseline_api.db.models.user import User
from baseline_api.db.session import get_db_session
from baseline_api.privacy.user import get_single_user


class SingleUserContext:
    """Resolves the private single-user deployment context for API routes."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._user: User | None = None

    @property
    def user(self) -> User:
        if self._user is None:
            self._user = get_single_user(self._session)
        return self._user


def get_single_user_context(
    session: Annotated[Session, Depends(get_db_session)],
) -> SingleUserContext:
    return SingleUserContext(session)
