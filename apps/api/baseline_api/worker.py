"""arq worker settings for Baseline background jobs."""

import datetime as dt
from typing import Any

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func
from sqlmodel import col, select

from baseline_api.briefing.worker import daily_briefing, daily_briefing_cron
from baseline_api.config import get_settings
from baseline_api.db.models import DailyAnalysisJob
from baseline_api.features.worker import (
    daily_analysis,
)
from baseline_api.features.worker import (
    on_shutdown as features_on_shutdown,
)
from baseline_api.features.worker import (
    on_startup as features_on_startup,
)
from baseline_api.ingestion.normalization.worker import (
    normalize_health_batch,
)
from baseline_api.memory.worker import (
    compact_monthly_memory,
    compact_quarterly_memory,
    compact_weekly_memory,
)
from baseline_api.schemas.enums import AnalysisJobStatus

STALE_RUNNING_DAILY_BRIEFING_SECONDS = 60 * 60


async def on_startup(ctx: dict[str, Any]) -> None:
    await features_on_startup(ctx)
    mark_stale_running_daily_briefing_jobs_failed(ctx)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await features_on_shutdown(ctx)


def mark_stale_running_daily_briefing_jobs_failed(
    ctx: dict[str, Any],
    *,
    now: dt.datetime | None = None,
) -> int:
    session_maker = ctx["session_maker"]
    recorded_at = now or dt.datetime.now(dt.UTC)
    cutoff = recorded_at - dt.timedelta(seconds=STALE_RUNNING_DAILY_BRIEFING_SECONDS)
    recovered = 0

    with session_maker() as session:
        stale_jobs = session.exec(
            select(DailyAnalysisJob)
            .where(DailyAnalysisJob.status == AnalysisJobStatus.running.value)
            .where(col(DailyAnalysisJob.started_at) < cutoff)
        ).all()
        for job in stale_jobs:
            job.status = AnalysisJobStatus.failed.value
            job.error_code = "analysis_worker_restarted"
            job.error_message = "Daily briefing worker restarted after this job was left running."
            job.completed_at = recorded_at
            job.stage_trace = [
                *job.stage_trace,
                {
                    "stage": "job_failed",
                    "status": "failed",
                    "error_code": job.error_code,
                    "trace_id": job.request_trace_id,
                    "job_id": str(job.id),
                    "recorded_at": recorded_at.isoformat(),
                },
            ]
            session.add(job)
            recovered += 1
        if recovered:
            session.commit()
    return recovered


class WorkerSettings:
    """arq worker configuration.

    Run with:
        python -m arq baseline_api.worker.WorkerSettings
    """

    functions = [normalize_health_batch, daily_analysis, func(daily_briefing, max_tries=1)]
    cron_jobs = [
        cron(daily_briefing_cron, hour=8, minute=0, max_tries=1),
        cron(compact_weekly_memory, weekday="mon", hour=6, minute=0, max_tries=1),
        cron(compact_monthly_memory, day=1, hour=6, minute=0, max_tries=1),
        cron(
            compact_quarterly_memory,
            month={1, 4, 7, 10},
            day=1,
            hour=6,
            minute=30,
            max_tries=1,
        ),
    ]
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    on_startup = on_startup
    on_shutdown = on_shutdown
