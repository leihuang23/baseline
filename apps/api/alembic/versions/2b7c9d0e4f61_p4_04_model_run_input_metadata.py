"""p4_04_model_run_input_metadata

Revision ID: 2b7c9d0e4f61
Revises: 9e2a4c7d8f01
Create Date: 2026-07-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2b7c9d0e4f61"
down_revision: str | None = "9e2a4c7d8f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_run",
        sa.Column(
            "input_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("model_run", "input_metadata", server_default=None)


def downgrade() -> None:
    op.drop_column("model_run", "input_metadata")
