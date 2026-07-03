"""arq worker settings for Baseline background jobs."""

from typing import Any

from arq.connections import RedisSettings

from baseline_api.config import get_settings
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


async def on_startup(ctx: dict[str, Any]) -> None:
    await features_on_startup(ctx)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await features_on_shutdown(ctx)


class WorkerSettings:
    """arq worker configuration.

    Run with:
        python -m arq baseline_api.worker.WorkerSettings
    """

    functions = [normalize_health_batch, daily_analysis]
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    on_startup = on_startup
    on_shutdown = on_shutdown
