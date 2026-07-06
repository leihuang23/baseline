"""Migration round-trip tests against a throwaway Postgres database."""

import datetime as dt
import os
from uuid import uuid4

import pytest
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
    assert "backfill_job" in tables
    assert "daily_data_quality" in tables
    assert "normalized_health_metric_source_sample" in tables
    assert "derived_daily_feature" in tables
    assert "recommendation" in tables
    assert "knowledge_chunk" in tables

    command.downgrade(alembic_cfg, "base")

    with db_engine.connect() as conn:
        result = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        )
        tables_after_downgrade = {row[0] for row in result}

    assert "user" not in tables_after_downgrade
    assert "raw_health_sample" not in tables_after_downgrade
    assert "normalized_health_metric_source_sample" not in tables_after_downgrade
    assert "knowledge_chunk" not in tables_after_downgrade

    command.upgrade(alembic_cfg, "head")


def test_p5_01_migration_rejects_incomplete_existing_knowledge_sources(db_engine) -> None:
    """P5-01 must fail clearly before making incomplete source metadata non-null."""
    os.environ["DATABASE_URL"] = str(db_engine.url)
    alembic_cfg = Config("alembic.ini")

    try:
        command.downgrade(alembic_cfg, "base")
        command.upgrade(alembic_cfg, "d087276a0c2b")

        with db_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO knowledge_source (
                        id,
                        created_at,
                        updated_at,
                        title,
                        source_type,
                        ingested_at,
                        version,
                        trust_level
                    )
                    VALUES (
                        :id,
                        :created_at,
                        :updated_at,
                        :title,
                        :source_type,
                        :ingested_at,
                        :version,
                        :trust_level
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "created_at": dt.datetime(2024, 1, 1),
                    "updated_at": dt.datetime(2024, 1, 1),
                    "title": "Incomplete legacy source",
                    "source_type": "article",
                    "ingested_at": dt.datetime(2024, 1, 2),
                    "version": "v1",
                    "trust_level": "curated",
                },
            )

        with pytest.raises(RuntimeError, match="complete knowledge_source metadata"):
            command.upgrade(alembic_cfg, "5a2d1e4f7b90")
    finally:
        command.downgrade(alembic_cfg, "base")
        command.upgrade(alembic_cfg, "head")


def test_retry_count_migration_adds_default_for_existing_jobs(db_engine) -> None:
    """Adding retry_count to daily_analysis_job must not fail when rows already exist."""
    os.environ["DATABASE_URL"] = str(db_engine.url)
    alembic_cfg = Config("alembic.ini")

    try:
        command.downgrade(alembic_cfg, "base")
        command.upgrade(alembic_cfg, "5a2d1e4f7b90")

        user_id = uuid4()
        job_id = uuid4()
        with db_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO "user" (
                        id, created_at, updated_at, timezone, locale,
                        privacy_mode, active_consent_version
                    )
                    VALUES (
                        :user_id, :created_at, :updated_at, 'UTC', 'en', 'cloud_assisted', 'v1'
                    )
                    """
                ),
                {
                    "user_id": str(user_id),
                    "created_at": dt.datetime(2024, 1, 1),
                    "updated_at": dt.datetime(2024, 1, 1),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO daily_analysis_job (
                        id, created_at, updated_at, user_id, date, status,
                        force_recompute, include_external_knowledge, privacy_mode, request_trace_id,
                        stage_trace
                    )
                    VALUES (
                        :job_id, :created_at, :updated_at, :user_id, :date, 'completed',
                        false, false, 'cloud_assisted', 'trace-1',
                        '[]'::jsonb
                    )
                    """
                ),
                {
                    "job_id": str(job_id),
                    "created_at": dt.datetime(2024, 1, 1),
                    "updated_at": dt.datetime(2024, 1, 1),
                    "user_id": str(user_id),
                    "date": dt.date(2024, 1, 1),
                },
            )

        command.upgrade(alembic_cfg, "head")

        with db_engine.connect() as conn:
            result = conn.execute(
                text("SELECT retry_count FROM daily_analysis_job WHERE id = :job_id"),
                {"job_id": str(job_id)},
            )
            row = result.fetchone()

        assert row is not None
        assert row[0] == 0
    finally:
        command.downgrade(alembic_cfg, "base")
        command.upgrade(alembic_cfg, "head")
