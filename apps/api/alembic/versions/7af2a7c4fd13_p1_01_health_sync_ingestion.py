"""p1_01_health_sync_ingestion

Revision ID: 7af2a7c4fd13
Revises: d087276a0c2b
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7af2a7c4fd13"
down_revision: str | None = "d087276a0c2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_import_batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("client_sync_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("request_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_platform", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_device", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("timezone", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("last_anchor", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("next_anchor", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data_quality_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalization_job_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_sync_id", name="uq_health_import_batch_user_client"),
    )
    op.create_index(
        "ix_health_import_batch_user_id_imported_at",
        "health_import_batch",
        ["user_id", "imported_at"],
        unique=False,
    )
    op.add_column(
        "raw_health_sample",
        sa.Column(
            "content_hash",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default="",
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_raw_health_sample_source_hash",
        "raw_health_sample",
        ["user_id", "source_platform", "source_sample_id", "content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_raw_health_sample_source_hash",
        "raw_health_sample",
        type_="unique",
    )
    op.drop_column("raw_health_sample", "content_hash")
    op.drop_index(
        "ix_health_import_batch_user_id_imported_at",
        table_name="health_import_batch",
    )
    op.drop_table("health_import_batch")
