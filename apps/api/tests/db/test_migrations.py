"""Migration round-trip tests against a throwaway Postgres database."""

import os

from alembic.config import Config
from sqlalchemy import text

from alembic import command


def test_migration_upgrade_downgrade_round_trip(db_engine) -> None:
    """Alembic upgrade head followed by downgrade base must be idempotent."""
    os.environ["DATABASE_URL"] = str(db_engine.url)
    alembic_cfg = Config("alembic.ini")

    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    with db_engine.connect() as conn:
        result = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        )
        tables = {row[0] for row in result}

    assert "user" in tables
    assert "raw_health_sample" in tables
    assert "derived_daily_feature" in tables
    assert "recommendation" in tables
