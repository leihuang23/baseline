"""Versioned API endpoints for daily analysis, briefings, and feedback."""

import datetime as dt
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from baseline_api.api.deps import SingleUserContext, get_single_user_context
from baseline_api.briefing import BriefingError, DailyBriefingService, LLMExplainer
from baseline_api.briefing.queue import ArqDailyBriefingJobQueue, DailyBriefingJobQueue
from baseline_api.config import Settings
from baseline_api.db.session import get_db_session
from baseline_api.feedback import FeedbackError, FeedbackService
from baseline_api.llm.factory import build_default_router
from baseline_api.llm.orchestrator import LLMOrchestrator
from baseline_api.privacy import PrivacyError
from baseline_api.schemas.api import (
    BriefingTraceInspection,
    DailyAnalysisRequest,
    DailyAnalysisResponse,
    DailyBriefingResponse,
    RecommendationFeedbackRequest,
    RecommendationFeedbackResponse,
)
from baseline_api.schemas.common import APIEnvelope, APIError

router = APIRouter(prefix="/v1", tags=["contracts"])

FEEDBACK_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": APIEnvelope[None],
        "description": "Recommendation was not found.",
    },
}


def _llm_explainer(request: Request, session: Session) -> LLMExplainer | None:
    explainer = getattr(request.app.state, "briefing_llm_explainer", None)
    if explainer is not None:
        return explainer  # type: ignore[no-any-return]

    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        return None
    return LLMOrchestrator(
        session=session,
        router=build_default_router(settings),
    )


def _error_response(error: BriefingError | PrivacyError) -> JSONResponse:
    envelope: APIEnvelope[None] = APIEnvelope(
        status="error",
        error=APIError(code=error.code, message=error.message, details=error.details),
    )
    return JSONResponse(
        status_code=error.status_code,
        content=envelope.model_dump(mode="json"),
    )


def _feedback_error_response(error: FeedbackError) -> JSONResponse:
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
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[DailyAnalysisResponse] | JSONResponse:
    service = DailyBriefingService(
        session,
        llm_explainer=_llm_explainer(request, session),
        settings=request.app.state.settings,
    )
    try:
        job = service.get_or_create_daily_job_for_date(
            payload.date,
            user=context.user,
            force_recompute=payload.force_recompute,
            include_external_knowledge=payload.include_external_knowledge,
            privacy_mode=payload.privacy_mode,
        )
        if getattr(request.app.state, "briefing_run_inline", False):
            data = await service.run_daily_job(job.id)
        else:
            queue = _daily_briefing_queue(request)
            try:
                await queue.enqueue_daily_briefing(job_id=job.id)
            except Exception as exc:
                service.mark_daily_job_failed(
                    job.id,
                    error_code="analysis_enqueue_failed",
                    error_message=exc.__class__.__name__,
                )
                raise BriefingError(
                    code="analysis_enqueue_failed",
                    message="Daily briefing job could not be queued.",
                    status_code=503,
                ) from exc
            data = DailyAnalysisResponse(
                analysis_job_id=job.id,
                status=job.status,
                estimated_completion_seconds=request.app.state.settings.daily_briefing_estimate_seconds,
            )
    except BriefingError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


def _daily_briefing_queue(request: Request) -> DailyBriefingJobQueue:
    queue = getattr(request.app.state, "daily_briefing_queue", None)
    if queue is not None:
        return queue  # type: ignore[no-any-return]

    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Application settings are not initialized.")
    return ArqDailyBriefingJobQueue(str(settings.redis_url))


@router.get("/analysis/daily/{job_id}", response_model=APIEnvelope[DailyAnalysisResponse])
async def get_daily_analysis_job(
    job_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[DailyAnalysisResponse] | JSONResponse:
    service = DailyBriefingService(session, settings=request.app.state.settings)
    try:
        data = service.get_daily_job(job_id, user=context.user)
    except BriefingError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/analysis/traces/{trace_id}", response_model=APIEnvelope[BriefingTraceInspection])
async def get_analysis_trace(
    trace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[BriefingTraceInspection] | JSONResponse:
    service = DailyBriefingService(session, settings=request.app.state.settings)
    try:
        data = service.get_trace(trace_id, user=context.user)
    except BriefingError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.get("/briefings/{date}", response_model=APIEnvelope[DailyBriefingResponse])
async def get_daily_briefing(
    date: dt.date,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
    offline_last: bool = False,
) -> APIEnvelope[DailyBriefingResponse] | JSONResponse:
    service = DailyBriefingService(session, settings=request.app.state.settings)
    try:
        data = service.get_briefing(target_date=date, offline_last=offline_last, user=context.user)
    except BriefingError as error:
        return _error_response(error)
    except PrivacyError as error:
        return _error_response(error)
    return APIEnvelope(status="success", data=data)


@router.post(
    "/recommendations/{id}/feedback",
    response_model=APIEnvelope[RecommendationFeedbackResponse],
    responses=FEEDBACK_ERROR_RESPONSES,
)
async def submit_recommendation_feedback(
    id: UUID,
    payload: RecommendationFeedbackRequest,
    session: Annotated[Session, Depends(get_db_session)],
    context: Annotated[SingleUserContext, Depends(get_single_user_context)],
) -> APIEnvelope[RecommendationFeedbackResponse] | JSONResponse:
    service = FeedbackService(session)
    try:
        data = service.submit_feedback(id, payload, user=context.user)
    except FeedbackError as error:
        return _feedback_error_response(error)
    return APIEnvelope(status="success", data=data)
