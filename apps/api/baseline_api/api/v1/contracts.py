"""Versioned contract stubs for API endpoints whose behavior lands in later slices."""

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlmodel import Session

from baseline_api.briefing import BriefingError, DailyBriefingService, LLMExplainer
from baseline_api.config import Settings
from baseline_api.db.session import get_db_session
from baseline_api.llm.factory import build_default_router
from baseline_api.llm.orchestrator import LLMOrchestrator
from baseline_api.schemas.api import (
    BriefingTraceInspection,
    DailyAnalysisRequest,
    DailyAnalysisResponse,
    DailyBriefingResponse,
    DataExportRequest,
    DataExportResponse,
    RecommendationFeedbackRequest,
    RecommendationFeedbackResponse,
)
from baseline_api.schemas.common import APIEnvelope, APIError, not_implemented_envelope

router = APIRouter(prefix="/v1", tags=["contracts"])


def _stub(response: Response) -> APIEnvelope[None]:
    response.status_code = status.HTTP_501_NOT_IMPLEMENTED
    return not_implemented_envelope()


def _llm_explainer(request: Request, session: Session) -> LLMExplainer | None:
    explainer = getattr(request.app.state, "briefing_llm_explainer", None)
    if explainer is not None:
        return explainer  # type: ignore[no-any-return]

    settings = request.app.state.settings
    if not isinstance(settings, Settings) or not settings.deepseek_api_key:
        return None
    return LLMOrchestrator(
        session=session,
        router=build_default_router(settings),
    )


def _error_response(error: BriefingError) -> JSONResponse:
    envelope: APIEnvelope[None] = APIEnvelope(
        status="error",
        error=APIError(code=error.code, message=error.message, details=error.details),
    )
    return JSONResponse(
        status_code=error.status_code,
        content=envelope.model_dump(mode="json"),
    )


@router.post("/analysis/daily", response_model=APIEnvelope[DailyAnalysisResponse])
async def generate_daily_analysis(
    payload: DailyAnalysisRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_db_session)],
) -> APIEnvelope[DailyAnalysisResponse] | JSONResponse:
    service = DailyBriefingService(
        session,
        llm_explainer=_llm_explainer(request, session),
    )
    try:
        job = service.create_daily_job(payload)
        if getattr(request.app.state, "briefing_run_inline", False):
            data = await service.run_daily_job(job.id)
        else:
            settings = request.app.state.settings
            if not isinstance(settings, Settings):
                raise RuntimeError("Application settings are not initialized.")
            background_tasks.add_task(
                _run_daily_analysis_job,
                settings,
                job.id,
                getattr(request.app.state, "briefing_llm_explainer", None),
            )
            data = DailyAnalysisResponse(
                analysis_job_id=job.id,
                status=job.status,
                estimated_completion_seconds=30,
            )
    except BriefingError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/analysis/daily/{job_id}", response_model=APIEnvelope[DailyAnalysisResponse])
async def get_daily_analysis_job(
    job_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> APIEnvelope[DailyAnalysisResponse] | JSONResponse:
    service = DailyBriefingService(session)
    try:
        data = service.get_daily_job(job_id)
    except BriefingError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/analysis/traces/{trace_id}", response_model=APIEnvelope[BriefingTraceInspection])
async def get_analysis_trace(
    trace_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> APIEnvelope[BriefingTraceInspection] | JSONResponse:
    service = DailyBriefingService(session)
    try:
        data = service.get_trace(trace_id)
    except BriefingError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/briefings/{date}", response_model=APIEnvelope[DailyBriefingResponse])
async def get_daily_briefing(
    date: dt.date,
    session: Annotated[Session, Depends(get_db_session)],
    offline_last: bool = False,
) -> APIEnvelope[DailyBriefingResponse] | JSONResponse:
    service = DailyBriefingService(session)
    try:
        data = service.get_briefing(target_date=date, offline_last=offline_last)
    except BriefingError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.post(
    "/recommendations/{id}/feedback",
    response_model=APIEnvelope[RecommendationFeedbackResponse],
)
async def submit_recommendation_feedback(
    id: UUID,
    request: RecommendationFeedbackRequest,
    response: Response,
) -> APIEnvelope[None]:
    return _stub(response)


@router.post("/data/export", response_model=APIEnvelope[DataExportResponse])
async def export_data(
    request: DataExportRequest,
    response: Response,
) -> APIEnvelope[None]:
    return _stub(response)


async def _run_daily_analysis_job(
    settings: Settings,
    job_id: UUID,
    llm_explainer: LLMExplainer | None,
) -> None:
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            explainer = llm_explainer
            if explainer is None and settings.deepseek_api_key:
                explainer = LLMOrchestrator(
                    session=session,
                    router=build_default_router(settings),
                )
            await DailyBriefingService(session, llm_explainer=explainer).run_daily_job(job_id)
    finally:
        engine.dispose()
