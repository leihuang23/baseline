"""p5_01_knowledge_corpus

Revision ID: 5a2d1e4f7b90
Revises: 2b7c9d0e4f61
Create Date: 2026-07-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a2d1e4f7b90"
down_revision: str | None = "2b7c9d0e4f61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _reject_incomplete_knowledge_sources()
    op.alter_column(
        "knowledge_source",
        "author_or_org",
        existing_type=sqlmodel.sql.sqltypes.AutoString(),
        nullable=False,
    )
    op.alter_column(
        "knowledge_source",
        "url_or_identifier",
        existing_type=sqlmodel.sql.sqltypes.AutoString(),
        nullable=False,
    )
    op.alter_column(
        "knowledge_source",
        "license_status",
        existing_type=sqlmodel.sql.sqltypes.AutoString(),
        nullable=False,
    )
    op.alter_column(
        "knowledge_source",
        "published_at",
        existing_type=sa.Date(),
        nullable=False,
    )
    op.alter_column(
        "knowledge_source",
        "ingested_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="ingested_at AT TIME ZONE 'UTC'",
    )
    op.add_column(
        "knowledge_source",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "knowledge_source",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_knowledge_source_active_identifier",
        "knowledge_source",
        ["url_or_identifier", "superseded_at", "removed_at"],
        unique=False,
    )

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(embedding) = 'array' AND jsonb_array_length(embedding) = 16",
            name="ck_knowledge_chunk_embedding_dimension",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_source.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "chunk_index", name="uq_knowledge_chunk_source_index"),
    )
    op.create_index(
        "ix_knowledge_chunk_content_hash",
        "knowledge_chunk",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_chunk_source_id",
        "knowledge_chunk",
        ["source_id"],
        unique=False,
    )


def _reject_incomplete_knowledge_sources() -> None:
    incomplete_count = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT count(*)
            FROM knowledge_source
            WHERE author_or_org IS NULL
               OR url_or_identifier IS NULL
               OR license_status IS NULL
               OR published_at IS NULL
            """
            )
        )
        .scalar_one()
    )
    if incomplete_count:
        raise RuntimeError(
            "P5-01 migration requires complete knowledge_source metadata before upgrade; "
            "backfill or remove incomplete rows first"
        )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunk_source_id", table_name="knowledge_chunk")
    op.drop_index("ix_knowledge_chunk_content_hash", table_name="knowledge_chunk")
    op.drop_table("knowledge_chunk")
    op.drop_index("ix_knowledge_source_active_identifier", table_name="knowledge_source")
    op.drop_column("knowledge_source", "removed_at")
    op.drop_column("knowledge_source", "superseded_at")
    op.alter_column(
        "knowledge_source",
        "ingested_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=False,
        postgresql_using="ingested_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "knowledge_source",
        "published_at",
        existing_type=sa.Date(),
        nullable=True,
    )
    op.alter_column(
        "knowledge_source",
        "license_status",
        existing_type=sqlmodel.sql.sqltypes.AutoString(),
        nullable=True,
    )
    op.alter_column(
        "knowledge_source",
        "url_or_identifier",
        existing_type=sqlmodel.sql.sqltypes.AutoString(),
        nullable=True,
    )
    op.alter_column(
        "knowledge_source",
        "author_or_org",
        existing_type=sqlmodel.sql.sqltypes.AutoString(),
        nullable=True,
    )
