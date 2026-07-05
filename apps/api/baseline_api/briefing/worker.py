"""arq worker functions for daily briefing generation."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from baseline_api.briefing.service import DailyBriefingService
from baseline_api.config import get_settings
from baseline_api.llm.factory import build_default_router
from baseline_api.llm.orchestrator import LLMOrchestrator


async def daily_briefing(ctx: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Run the full persisted daily briefing pipeline in the durable worker."""

    session_maker: sessionmaker[Session] = ctx["session_maker"]
    settings = get_settings()
    job_uuid = UUID(job_id)
    with session_maker() as session:
        result = await DailyBriefingService(
            session,
            llm_explainer=LLMOrchestrator(
                session=session,
                router=build_default_router(settings),
            ),
        ).run_daily_job(job_uuid)
        return result.model_dump(mode="json")
