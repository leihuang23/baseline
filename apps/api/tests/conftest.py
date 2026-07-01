"""Shared test fixtures for Baseline API tests."""

import os
from collections.abc import Generator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlmodel import Session

from alembic import command

TEST_DATABASE_NAME = "baseline_test_p0_02"


def _base_database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://baseline@localhost:5433/baseline",
    )


def _test_database_url() -> str:
    return _base_database_url().rsplit("/", 1)[0] + f"/{TEST_DATABASE_NAME}"


@pytest.fixture(scope="session")
def db_engine() -> Generator:
    """Create a throwaway test database, migrate it, and tear it down."""
    base_url = _base_database_url()
    admin_engine = create_engine(base_url)

    with admin_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DATABASE_NAME}"))
        conn.execute(text(f"CREATE DATABASE {TEST_DATABASE_NAME}"))
    admin_engine.dispose()

    # Run Alembic migrations against the test database.
    test_url = _test_database_url()
    os.environ["DATABASE_URL"] = test_url
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(test_url)
    yield engine

    engine.dispose()

    # Tear down the test database.
    admin_engine = create_engine(base_url)
    with admin_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(
            text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :db_name AND pid <> pg_backend_pid()
                """
            ),
            {"db_name": TEST_DATABASE_NAME},
        )
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DATABASE_NAME}"))
    admin_engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Generator[Session]:
    """Provide a transactional SQLModel session that rolls back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
