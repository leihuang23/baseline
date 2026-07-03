"""arq worker settings for Baseline background jobs."""

from arq.connections import RedisSettings

from baseline_api.config import get_settings
from baseline_api.ingestion.normalization.worker import (
    normalize_health_batch,
    on_shutdown,
    on_startup,
)


class WorkerSettings:
    """arq worker configuration.

    Run with:
        python -m arq baseline_api.worker.WorkerSettings
    """

    functions = [normalize_health_batch]
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    on_startup = on_startup
    on_shutdown = on_shutdown
