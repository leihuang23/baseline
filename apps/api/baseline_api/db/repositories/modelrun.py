"""Model run repository."""

from sqlmodel import Session

from baseline_api.db.models.modelrun import ModelRun
from baseline_api.db.repositories.base import BaseRepository


class ModelRunRepository(BaseRepository[ModelRun]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ModelRun)
