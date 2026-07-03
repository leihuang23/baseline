"""Database session dependencies for API routes."""

from collections.abc import Generator
from functools import lru_cache

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlmodel import Session

from baseline_api.config import Settings


@lru_cache
def _engine(database_url: str) -> Engine:
    return create_engine(database_url)


def get_db_session(request: Request) -> Generator[Session]:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Application settings are not initialized.")

    with Session(_engine(str(settings.database_url))) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
