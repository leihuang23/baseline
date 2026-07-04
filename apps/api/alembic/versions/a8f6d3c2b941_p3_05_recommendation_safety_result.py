"""p3_05_recommendation_safety_result

Revision ID: a8f6d3c2b941
Revises: 0d36a7ec2f19
Create Date: 2026-07-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8f6d3c2b941"
down_revision: str | None = "0d36a7ec2f19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recommendation",
        sa.Column(
            "safety_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("recommendation", "safety_result", server_default=None)


def downgrade() -> None:
    op.drop_column("recommendation", "safety_result")
