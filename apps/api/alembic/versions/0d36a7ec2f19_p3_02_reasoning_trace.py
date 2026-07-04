"""p3_02_reasoning_trace

Revision ID: 0d36a7ec2f19
Revises: c3a9b7d2e401
Create Date: 2026-07-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0d36a7ec2f19"
down_revision: str | None = "c3a9b7d2e401"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reasoning_trace",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("trace_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("assessment_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("input_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("rules_fired", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hard_safety_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reasoning_trace_user_id_date",
        "reasoning_trace",
        ["user_id", "date"],
        unique=False,
    )
    op.add_column(
        "readiness_assessment",
        sa.Column(
            "candidate_options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "readiness_assessment",
        sa.Column(
            "follow_up_questions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "readiness_assessment",
        sa.Column(
            "hard_safety_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("readiness_assessment", "candidate_options", server_default=None)
    op.alter_column("readiness_assessment", "follow_up_questions", server_default=None)
    op.alter_column("readiness_assessment", "hard_safety_flags", server_default=None)


def downgrade() -> None:
    op.drop_column("readiness_assessment", "hard_safety_flags")
    op.drop_column("readiness_assessment", "follow_up_questions")
    op.drop_column("readiness_assessment", "candidate_options")
    op.drop_index("ix_reasoning_trace_user_id_date", table_name="reasoning_trace")
    op.drop_table("reasoning_trace")
