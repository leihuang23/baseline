"""Versioned contract stubs for API endpoints whose behavior lands in later slices."""

import datetime as dt
from uuid import UUID

from fastapi import APIRouter, Response, status

from baseline_api.schemas.api import (
    AssistantQueryRequest,
    AssistantQueryResponse,
    DailyAnalysisRequest,
    DailyAnalysisResponse,
    DailyBriefingResponse,
    DailyCheckInRequest,
    DailyCheckInResponse,
    DataExportRequest,
    DataExportResponse,
    HealthSyncRequest,
    HealthSyncResponse,
    RecommendationFeedbackRequest,
    RecommendationFeedbackResponse,
)
from baseline_api.schemas.common import APIEnvelope, not_implemented_envelope

router = APIRouter(prefix="/v1", tags=["contracts"])


def _stub(response: Response) -> APIEnvelope[None]:
    response.status_code = status.HTTP_501_NOT_IMPLEMENTED
    return not_implemented_envelope()


@router.post("/health/sync", response_model=APIEnvelope[HealthSyncResponse])
async def sync_health(
    request: HealthSyncRequest,
    response: Response,
) -> APIEnvelope[None]:
    return _stub(response)


@router.post("/checkins/daily", response_model=APIEnvelope[DailyCheckInResponse])
async def submit_daily_checkin(
    request: DailyCheckInRequest,
    response: Response,
) -> APIEnvelope[None]:
    return _stub(response)


@router.post("/analysis/daily", response_model=APIEnvelope[DailyAnalysisResponse])
async def generate_daily_analysis(
    request: DailyAnalysisRequest,
    response: Response,
) -> APIEnvelope[None]:
    return _stub(response)


@router.get("/briefings/{date}", response_model=APIEnvelope[DailyBriefingResponse])
async def get_daily_briefing(
    date: dt.date,
    response: Response,
) -> APIEnvelope[None]:
    return _stub(response)


@router.post("/assistant/query", response_model=APIEnvelope[AssistantQueryResponse])
async def ask_assistant(
    request: AssistantQueryRequest,
    response: Response,
) -> APIEnvelope[None]:
    return _stub(response)


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
