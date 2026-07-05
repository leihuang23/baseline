"""Versioned observability query endpoints."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from baseline_api.config import Settings
from baseline_api.db.session import get_db_session
from baseline_api.observability import evaluate_configured_operational_alerts
from baseline_api.schemas.common import APIEnvelope

router = APIRouter(prefix="/v1/observability", tags=["observability"])


@router.get("/alerts", response_model=APIEnvelope[list[dict[str, Any]]])
async def get_operational_alerts(
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    since: dt.datetime | None = None,
) -> APIEnvelope[list[dict[str, Any]]]:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Application settings are not initialized.")
    alerts = evaluate_configured_operational_alerts(session, settings=settings, since=since)
    return APIEnvelope(status="success", data=[asdict(alert) for alert in alerts])
