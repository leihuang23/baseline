"""Shared non-table mixins for SQLModel entities."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BaseDBModel(SQLModel):
    """Common primary key and timestamp columns.

    This is a non-table mixin; subclasses declare `table=True`.
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=_utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=_utc_now, nullable=False)
