"""Shared test fixtures for Baseline API tests."""

import os
from collections.abc import Generator
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import Session

from alembic import command

REQUIRE_TEST_DB_ENV = "BASELINE_REQUIRE_TEST_DB"
TEST_DATABASE_NAME = "baseline_test_p0_02"
_DB_SKIP_REASON_ATTR = "_baseline_db_skip_reason"


def _base_database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://baseline@localhost:5433/baseline",
    )


def _test_database_url() -> str:
    return _base_database_url().rsplit("/", 1)[0] + f"/{TEST_DATABASE_NAME}"


def _requires_test_db(config: pytest.Config) -> bool:
    return bool(config.getoption("--require-db")) or os.environ.get(REQUIRE_TEST_DB_ENV) == "1"


def _db_engine(url: str) -> Engine:
    return create_engine(url, connect_args={"connect_timeout": 2})


def _database_unavailable_reason() -> str | None:
    engine = _db_engine(_base_database_url())
    try:
        with engine.connect():
            return None
    except Exception as exc:
        return f"test database unavailable at {_base_database_url()}: {exc}"
    finally:
        engine.dispose()


def _db_skip_reason(config: pytest.Config) -> str | None:
    return getattr(config, _DB_SKIP_REASON_ATTR, None)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-db",
        action="store_true",
        help="Fail collection when the integration test database is unavailable.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: requires a reachable Postgres test database")
    config.addinivalue_line(
        "markers",
        "require_db: fail collection instead of skipping when Postgres is unavailable",
    )
    reason = _database_unavailable_reason()
    setattr(config, _DB_SKIP_REASON_ATTR, reason)
    if reason is None:
        return
    if _requires_test_db(config):
        raise pytest.UsageError(reason)
    if hasattr(config.option, "cov_fail_under"):
        config.option.cov_fail_under = 0
    cov_plugin = config.pluginmanager.getplugin("_cov")
    if cov_plugin is not None:
        cov_plugin.options.cov_fail_under = 0


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    reason = _db_skip_reason(config)
    skip_db = pytest.mark.skip(reason=reason) if reason is not None else None
    required_db_items: list[str] = []
    for item in items:
        if {"db_engine", "db_session"} & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.db)
            if skip_db is not None:
                if item.get_closest_marker("require_db") is not None:
                    required_db_items.append(item.nodeid)
                    continue
                item.add_marker(skip_db)
    if required_db_items:
        sample = ", ".join(required_db_items[:3])
        if len(required_db_items) > 3:
            sample += f", ... ({len(required_db_items)} tests)"
        raise pytest.UsageError(f"{reason}; required DB tests cannot be skipped: {sample}")


@pytest.fixture(scope="session")
def db_engine(request: pytest.FixtureRequest) -> Generator[Engine]:
    """Create a throwaway test database, migrate it, and tear it down."""
    reason = _db_skip_reason(request.config)
    if reason is not None:
        pytest.skip(reason)

    base_url = _base_database_url()
    admin_engine = _db_engine(base_url)

    with admin_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DATABASE_NAME}"))
        conn.execute(text(f"CREATE DATABASE {TEST_DATABASE_NAME}"))
    admin_engine.dispose()

    # Run Alembic migrations against the test database.
    test_url = _test_database_url()
    os.environ["DATABASE_URL"] = test_url

    # Clear the cached settings so env changes above are picked up by migrations.
    from baseline_api.config import get_settings

    get_settings.cache_clear()

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    engine = _db_engine(test_url)
    yield engine

    engine.dispose()

    # Tear down the test database.
    admin_engine = _db_engine(base_url)
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
def db_session(db_engine: Any) -> Generator[Session]:
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
