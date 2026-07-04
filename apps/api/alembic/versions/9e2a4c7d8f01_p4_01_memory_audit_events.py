"""p4_01_memory_audit_events

Revision ID: 9e2a4c7d8f01
Revises: f6b7c8d9e012
Create Date: 2026-07-04 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e2a4c7d8f01"
down_revision: str | None = "f6b7c8d9e012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditeventtype ADD VALUE IF NOT EXISTS 'memory_corrected'")
    op.execute("ALTER TYPE auditeventtype ADD VALUE IF NOT EXISTS 'memory_deleted'")


def downgrade() -> None:
    # PostgreSQL enum values are intentionally retained on downgrade.
    pass
