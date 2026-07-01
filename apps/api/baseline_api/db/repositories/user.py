"""User and consent record repositories."""

from sqlmodel import Session

from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, User)


class ConsentRecordRepository(BaseRepository[ConsentRecord]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ConsentRecord)
