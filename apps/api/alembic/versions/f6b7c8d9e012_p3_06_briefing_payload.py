"""p3_06_briefing_payload

Revision ID: f6b7c8d9e012
Revises: a8f6d3c2b941
Create Date: 2026-07-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6b7c8d9e012"
down_revision: str | None = "a8f6d3c2b941"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_analysis_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("force_recompute", sa.Boolean(), nullable=False),
        sa.Column("include_external_knowledge", sa.Boolean(), nullable=False),
        sa.Column("privacy_mode", sa.String(), nullable=False),
        sa.Column("request_trace_id", sa.String(), nullable=False),
        sa.Column("reasoning_trace_id", sa.Uuid(), nullable=True),
        sa.Column("recommendation_id", sa.Uuid(), nullable=True),
        sa.Column("stage_trace", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["reasoning_trace_id"], ["reasoning_trace.id"]),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendation.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_daily_analysis_job_user_id_date",
        "daily_analysis_job",
        ["user_id", "date"],
        unique=False,
    )
    op.add_column(
        "recommendation",
        sa.Column("reasoning_trace_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "recommendation",
        sa.Column(
            "briefing_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_foreign_key(
        "fk_recommendation_reasoning_trace_id_reasoning_trace",
        "recommendation",
        "reasoning_trace",
        ["reasoning_trace_id"],
        ["id"],
    )
    op.alter_column("recommendation", "briefing_payload", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "fk_recommendation_reasoning_trace_id_reasoning_trace",
        "recommendation",
        type_="foreignkey",
    )
    op.drop_column("recommendation", "briefing_payload")
    op.drop_column("recommendation", "reasoning_trace_id")
    op.drop_index("ix_daily_analysis_job_user_id_date", table_name="daily_analysis_job")
    op.drop_table("daily_analysis_job")
