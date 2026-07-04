"""View-data-sent disclosure service."""

from __future__ import annotations

from sqlmodel import Session, col, select

from baseline_api.db.models.modelrun import ModelRun
from baseline_api.privacy.model_runs import sanitize_model_input_metadata
from baseline_api.privacy.user import get_single_user
from baseline_api.schemas.api import ModelDisclosureRecord, ModelDisclosureResponse


class ModelDisclosureService:
    """Reconstruct minimized outbound model payload metadata from ModelRun rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_model_payloads(self) -> ModelDisclosureResponse:
        user = get_single_user(self._session)
        rows = list(
            self._session.exec(
                select(ModelRun)
                .where(ModelRun.user_id == user.id)
                .order_by(col(ModelRun.created_at).desc())
            ).all()
        )
        return ModelDisclosureResponse(
            runs=[
                ModelDisclosureRecord(
                    run_id=row.id,
                    created_at=row.created_at,
                    run_type=row.run_type.value,
                    provider=row.model_provider,
                    model=row.model_name,
                    prompt_version=row.prompt_version,
                    schema_version=row.schema_version,
                    input_hash=row.input_hash,
                    payload_metadata=sanitize_model_input_metadata(row.input_metadata),
                )
                for row in rows
            ]
        )
