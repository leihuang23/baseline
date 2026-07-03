"""p2_03_unique_daily_features

Revision ID: c3a9b7d2e401
Revises: d04a8f918e51
Create Date: 2026-07-03 21:03:30.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3a9b7d2e401"
down_revision: str | None = "d04a8f918e51"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_derived_daily_feature_user_date",
        "derived_daily_feature",
        ["user_id", "date"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_derived_daily_feature_user_date",
        "derived_daily_feature",
        type_="unique",
    )
