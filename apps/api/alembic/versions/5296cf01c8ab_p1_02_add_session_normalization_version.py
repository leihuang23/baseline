"""p1_02_add_session_normalization_version

Revision ID: 5296cf01c8ab
Revises: 691c21a2a2eb
Create Date: 2026-07-03 16:05:45.223004

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5296cf01c8ab"
down_revision: str | None = "691c21a2a2eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sleep_session",
        sa.Column("normalization_version", sa.String(), nullable=False, server_default="p1-02-v1"),
    )
    op.add_column(
        "workout_session",
        sa.Column("normalization_version", sa.String(), nullable=False, server_default="p1-02-v1"),
    )


def downgrade() -> None:
    op.drop_column("workout_session", "normalization_version")
    op.drop_column("sleep_session", "normalization_version")
