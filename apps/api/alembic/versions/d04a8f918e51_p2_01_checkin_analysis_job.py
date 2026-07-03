"""p2_01_checkin_analysis_job

Revision ID: d04a8f918e51
Revises: b8d4f2c9a103
Create Date: 2026-07-03 19:04:21.147044

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d04a8f918e51"
down_revision: str | None = "b8d4f2c9a103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditeventtype ADD VALUE IF NOT EXISTS 'checkin_submitted'")
    op.execute("ALTER TYPE auditeventtype ADD VALUE IF NOT EXISTS 'checkin_updated'")
    op.execute("ALTER TYPE auditeventtype ADD VALUE IF NOT EXISTS 'checkin_deleted'")

    op.add_column(
        "daily_check_in",
        sa.Column("analysis_job_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "daily_check_in",
        sa.Column("free_text_note_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "daily_check_in",
        sa.Column(
            "redaction_status",
            sa.Enum(
                "redacted",
                "partial",
                "none",
                name="redactionstatus",
                native_enum=True,
                create_type=False,
            ),
            nullable=False,
            server_default="none",
        ),
    )
    op.alter_column("daily_check_in", "redaction_status", server_default=None)


def downgrade() -> None:
    op.drop_column("daily_check_in", "redaction_status")
    op.drop_column("daily_check_in", "free_text_note_summary")
    op.drop_column("daily_check_in", "analysis_job_id")
