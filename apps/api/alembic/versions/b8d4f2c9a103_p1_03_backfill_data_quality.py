"""p1_03_backfill_data_quality

Revision ID: b8d4f2c9a103
Revises: 5296cf01c8ab
Create Date: 2026-07-03 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d4f2c9a103"
down_revision: str | None = "5296cf01c8ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backfill_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_platform", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_device", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("timezone", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("chunk_days", sa.Integer(), nullable=False),
        sa.Column("next_start_date", sa.Date(), nullable=False),
        sa.Column("processed_days", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_platform",
            "start_date",
            "end_date",
            name="uq_backfill_job_user_source_range",
        ),
    )
    op.create_index(
        "ix_backfill_job_user_id_status",
        "backfill_job",
        ["user_id", "status"],
        unique=False,
    )

    op.create_table(
        "daily_data_quality",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("expected_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("present_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("missing_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("completeness_ratio", sa.Float(), nullable=False),
        sa.Column("completeness_warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("freshness_by_type", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stale_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date", name="uq_daily_data_quality_user_date"),
    )
    op.create_index(
        "ix_daily_data_quality_user_id_date",
        "daily_data_quality",
        ["user_id", "date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_daily_data_quality_user_id_date", table_name="daily_data_quality")
    op.drop_table("daily_data_quality")
    op.drop_index("ix_backfill_job_user_id_status", table_name="backfill_job")
    op.drop_table("backfill_job")
