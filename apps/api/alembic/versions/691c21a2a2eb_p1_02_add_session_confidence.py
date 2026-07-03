"""p1_02_add_session_confidence

Revision ID: 691c21a2a2eb
Revises: 7af2a7c4fd13
Create Date: 2026-07-03 15:48:23.890598

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "691c21a2a2eb"
down_revision: str | None = "7af2a7c4fd13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sleep_session",
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "workout_session",
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
    )


def downgrade() -> None:
    op.drop_column("workout_session", "confidence")
    op.drop_column("sleep_session", "confidence")
