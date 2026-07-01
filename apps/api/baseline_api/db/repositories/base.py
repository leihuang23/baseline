"""Base repository with minimal create/get/list helpers."""

from uuid import UUID

from sqlmodel import Session, SQLModel, select


class BaseRepository[T: SQLModel]:
    """Generic repository stub bound to a SQLModel table class."""

    def __init__(self, session: Session, model: type[T]) -> None:
        self.session = session
        self.model = model

    def create(self, instance: T) -> T:
        self.session.add(instance)
        self.session.flush()
        return instance

    def get_by_id(self, obj_id: UUID) -> T | None:
        return self.session.get(self.model, obj_id)

    def list_all(self, limit: int = 100) -> list[T]:
        statement = select(self.model).limit(limit)
        return list(self.session.exec(statement).all())
