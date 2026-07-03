"""arq worker function for the normalization job."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from baseline_api.config import get_settings
from baseline_api.ingestion.normalization.service import NormalizationService


async def normalize_health_batch(
    ctx: dict[str, Any],
    import_batch_id: str,
    user_id: str,
) -> dict[str, Any]:
    """arq job entrypoint: normalize raw samples for a single import batch."""

    session_maker: sessionmaker[Session] = ctx["session_maker"]
    with session_maker() as session:
        service = NormalizationService(session)
        result = service.normalize_batch(
            import_batch_id=UUID(import_batch_id),
            user_id=UUID(user_id),
        )
        session.commit()
    return result.model_dump(mode="json")


async def on_startup(ctx: dict[str, Any]) -> None:
    """Create a SQLAlchemy engine bound to the session maker."""

    settings = get_settings()
    engine = create_engine(str(settings.database_url))
    ctx["engine"] = engine
    ctx["session_maker"] = sessionmaker(bind=engine, class_=Session)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    """Dispose the SQLAlchemy engine on worker shutdown."""

    engine = ctx.get("engine")
    if engine is not None:
        engine.dispose()
