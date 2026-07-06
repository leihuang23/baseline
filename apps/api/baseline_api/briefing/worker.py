"""arq worker functions for daily briefing generation."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from baseline_api.briefing.service import DailyBriefingService
from baseline_api.config import get_settings
from baseline_api.llm.factory import build_default_router
from baseline_api.llm.orchestrator import LLMOrchestrator
from baseline_api.observability.alerts import stale_briefing_alert
from baseline_api.schemas.enums import PrivacyMode


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


async def daily_briefing_cron(ctx: dict[str, Any]) -> dict[str, Any]:
    """Fallback cron wrapper: create and run today's briefing if none exists."""

    session_maker: sessionmaker[Session] = ctx["session_maker"]
    settings = get_settings()
    today = dt.datetime.now(dt.UTC).date()
    with session_maker() as session:
        service = DailyBriefingService(
            session,
            llm_explainer=LLMOrchestrator(
                session=session,
                router=build_default_router(settings),
            ),
            settings=settings,
        )
        user = service._resolve_user()
        job = service.get_or_create_daily_job_for_date(
            today,
            user=user,
            privacy_mode=PrivacyMode(user.privacy_mode.value),
            include_external_knowledge=False,
            force_recompute=False,
        )
        result = await service.run_daily_job(job.id)
        alerts = stale_briefing_alert(
            session,
            settings=settings,
            since=None,
        )
        return {
            "status": "success",
            "date": today.isoformat(),
            "analysis_job_id": str(result.analysis_job_id),
            "job_status": result.status.value,
            "alerts": [alert.alert_type for alert in alerts],
        }
