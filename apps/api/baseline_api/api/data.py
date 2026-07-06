"""Data control and privacy API routes."""

from __future__ import annotations

import asyncio
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlmodel import Session

from baseline_api.api.deps import SingleUserContext, get_single_user_context
from baseline_api.db.session import get_db_session
from baseline_api.observability.logging import log_event
from baseline_api.privacy import (
    ConsentService,
    DataDeletionService,
    DataExportService,
    LocalExportStore,
    ModelDisclosureService,
    PrivacyError,
)
from baseline_api.schemas.api import (
    ConsentHistoryResponse,
    ConsentRecordRequest,
    ConsentRecordResponse,
    ConsentRevocationRequest,
    DataDeleteResponse,
    DataExportRequest,
    DataExportResponse,
    DisableExternalLLMRequest,
    LLMSettingsResponse,
    ModelDisclosureResponse,
)
from baseline_api.schemas.common import APIEnvelope, APIError

router = APIRouter(prefix="/v1/data", tags=["data-controls"])


async def get_export_store(request: Request) -> LocalExportStore:
    store = getattr(request.app.state, "export_store", None)
    if store is None:
        settings = request.app.state.settings
        store = LocalExportStore(
            settings.export_storage_dir,
            retention_hours=settings.export_retention_hours,
            app_env=settings.app_env,
        )
        request.app.state.export_store = store
    return cast(LocalExportStore, store)


async def _cleanup_expired_exports(store: LocalExportStore) -> None:
    try:
        removed = await asyncio.to_thread(store.cleanup_expired)
    except Exception as exc:
        log_event(
            "data_export_cleanup",
            status="failed",
            level="warning",
            error_class=type(exc).__name__,
        )
        return
    log_event(
        "data_export_cleanup",
        status="success",
        metadata={"removed_count": removed},
    )


@router.post("/export", response_model=APIEnvelope[DataExportResponse])
def export_data(
    payload: DataExportRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    store: Annotated[LocalExportStore, Depends(get_export_store)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[DataExportResponse] | Response:
    try:
        data = DataExportService(session, store).create_export(payload, user=context.user)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get(
    "/export/{export_job_id}/file",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
            "description": "Encrypted export file.",
        },
    },
)
def download_export(
    export_job_id: UUID,
    store: Annotated[LocalExportStore, Depends(get_export_store)],
) -> Response:
    try:
        stored = store.get(export_job_id)
    except PrivacyError as error:
        return _error_response(error)
    return Response(
        content=stored.path.read_bytes(),
        media_type=stored.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export_job_id}.export.enc"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/consent", response_model=APIEnvelope[ConsentRecordResponse])
def record_consent(
    payload: ConsentRecordRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> APIEnvelope[ConsentRecordResponse] | Response:
    try:
        data = ConsentService(session).record_consent(payload)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/consent/history", response_model=APIEnvelope[ConsentHistoryResponse])
def consent_history(
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[ConsentHistoryResponse] | Response:
    try:
        data = ConsentService(session).history(user=context.user)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.post(
    "/consent/disable-external-llm",
    response_model=APIEnvelope[ConsentRecordResponse],
)
def disable_external_llm(
    payload: DisableExternalLLMRequest,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[ConsentRecordResponse] | Response:
    try:
        data = ConsentService(session).disable_external_llm(payload, user=context.user)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.post("/consent/revoke", response_model=APIEnvelope[ConsentRecordResponse])
def revoke_consent(
    payload: ConsentRevocationRequest,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[ConsentRecordResponse] | Response:
    try:
        data = ConsentService(session).revoke(payload, user=context.user)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.delete("/all", response_model=APIEnvelope[DataDeleteResponse])
def delete_all_data(
    session: Annotated[Session, Depends(get_db_session)],
    store: Annotated[LocalExportStore, Depends(get_export_store)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[DataDeleteResponse] | Response:
    try:
        data = DataDeletionService(session, store).delete_all(user=context.user)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.delete("/checkins/{checkin_id}", response_model=APIEnvelope[DataDeleteResponse])
def delete_checkin(
    checkin_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[DataDeleteResponse] | Response:
    try:
        data = DataDeletionService(session).delete_checkin(checkin_id, user=context.user)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.delete("/checkins/{checkin_id}/note", response_model=APIEnvelope[DataDeleteResponse])
def delete_checkin_note(
    checkin_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[DataDeleteResponse] | Response:
    try:
        data = DataDeletionService(session).delete_note(checkin_id, user=context.user)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.delete(
    "/memory-summaries/{memory_summary_id}",
    response_model=APIEnvelope[DataDeleteResponse],
)
def delete_memory_summary(
    memory_summary_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[DataDeleteResponse] | Response:
    try:
        data = DataDeletionService(session).delete_memory_summary(
            memory_summary_id,
            user=context.user,
        )
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/model-disclosures", response_model=APIEnvelope[ModelDisclosureResponse])
def model_disclosures(
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[ModelDisclosureResponse] | Response:
    try:
        data = ModelDisclosureService(session).list_model_payloads(user=context.user)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/llm-settings", response_model=APIEnvelope[LLMSettingsResponse])
def llm_settings(
    request: Request,
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[LLMSettingsResponse] | Response:
    settings = request.app.state.settings
    data = LLMSettingsResponse(
        provider=settings.llm_default_provider,
        cheap_model=settings.llm_cheap_model,
        strong_model=settings.llm_strong_model,
        fallback_model=settings.llm_fallback_model,
    )
    return APIEnvelope(status="success", data=data)


def _error_response(error: PrivacyError) -> Response:
    envelope: APIEnvelope[None] = APIEnvelope(
        status="error",
        error=APIError(code=error.code, message=error.message, details=error.details),
    )
    return Response(
        status_code=error.status_code,
        content=envelope.model_dump_json(),
        media_type="application/json",
    )
