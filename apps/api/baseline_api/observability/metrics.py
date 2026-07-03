"""Prometheus metrics scaffold for Baseline operations."""

from collections.abc import Awaitable, Callable
from functools import wraps
from time import perf_counter
from typing import Any, ParamSpec, TypeVar

from fastapi import APIRouter, Response
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST

P = ParamSpec("P")
R = TypeVar("R")


registry = CollectorRegistry()


def _counter(name: str, documentation: str, labels: tuple[str, ...] = ()) -> Any:
    return Counter(name, documentation, labels, registry=registry)


def _histogram(name: str, documentation: str, labels: tuple[str, ...] = ()) -> Any:
    return Histogram(name, documentation, labels, registry=registry)


def _gauge(name: str, documentation: str, labels: tuple[str, ...] = ()) -> Any:
    return Gauge(name, documentation, labels, registry=registry)


sync_success = _counter("baseline_sync_success", "Successful sync operations.", ("source",))
sync_latency = _histogram("baseline_sync_latency_seconds", "Sync latency in seconds.", ("source",))
backfill_duration = _histogram(
    "baseline_backfill_duration_seconds",
    "Backfill duration.",
    ("source",),
)
duplicate_sample_rate = _gauge(
    "baseline_duplicate_sample_rate",
    "Duplicate sample ratio.",
    ("source",),
)
rejected_sample_count = _counter("baseline_rejected_sample", "Rejected samples.", ("reason",))
data_completeness_by_day = _gauge(
    "baseline_data_completeness_by_day_ratio",
    "Data completeness ratio by day.",
    ("day",),
)
data_staleness_flag = _gauge(
    "baseline_data_staleness_flag",
    "Data staleness flag by day and sample type.",
    ("day", "sample_type"),
)
feature_job_result = _counter("baseline_feature_job", "Feature job results.", ("status",))
llm_generation_result = _counter("baseline_llm_generation", "LLM generation results.", ("status",))
schema_validation_failure = _counter(
    "baseline_schema_validation_failure",
    "Schema validation failures.",
    ("schema_name",),
)
safety_block = _counter("baseline_safety_block", "Safety policy blocks.", ("category",))
recommendation_feedback = _counter(
    "baseline_recommendation_feedback",
    "Recommendation feedback distribution.",
    ("feedback",),
)
llm_token_usage = _counter("baseline_llm_token_usage", "LLM token usage.", ("model", "direction"))
llm_cost = _counter("baseline_llm_cost", "LLM cost.", ("model",))
briefing_latency = _histogram(
    "baseline_briefing_latency_seconds",
    "Briefing latency in seconds.",
)
qa_latency = _histogram("baseline_qa_latency_seconds", "Q&A latency in seconds.")

metrics_router = APIRouter(tags=["metrics"])


@metrics_router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


def count_calls(metric: Any, **labels: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate sync callables and increment a counter on success."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result = func(*args, **kwargs)
            _with_labels(metric, labels).inc()
            return result

        return wrapper

    return decorator


def time_calls(metric: Any, **labels: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate sync callables and observe elapsed seconds."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started_at = perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                _with_labels(metric, labels).observe(perf_counter() - started_at)

        return wrapper

    return decorator


def time_async_calls(
    metric: Any,
    **labels: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorate async callables and observe elapsed seconds."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started_at = perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                _with_labels(metric, labels).observe(perf_counter() - started_at)

        return wrapper

    return decorator


def increment_sync_success(*, source: str) -> None:
    sync_success.labels(source=source).inc()


def observe_sync_latency(seconds: float, *, source: str) -> None:
    sync_latency.labels(source=source).observe(seconds)


def observe_backfill_duration(seconds: float, *, source: str) -> None:
    backfill_duration.labels(source=source).observe(seconds)


def set_duplicate_sample_rate(rate: float, *, source: str) -> None:
    duplicate_sample_rate.labels(source=source).set(rate)


def increment_rejected_sample_count(*, reason: str) -> None:
    rejected_sample_count.labels(reason=reason).inc()


def set_data_completeness_by_day(ratio: float, *, day: str) -> None:
    data_completeness_by_day.labels(day=day).set(ratio)


def set_data_staleness_flag(is_stale: bool, *, day: str, sample_type: str) -> None:
    data_staleness_flag.labels(day=day, sample_type=sample_type).set(float(is_stale))


def increment_feature_job_result(*, status: str) -> None:
    feature_job_result.labels(status=status).inc()


def increment_llm_generation_result(*, status: str) -> None:
    llm_generation_result.labels(status=status).inc()


def increment_schema_validation_failure(*, schema_name: str) -> None:
    schema_validation_failure.labels(schema_name=schema_name).inc()


def increment_safety_block(*, category: str) -> None:
    safety_block.labels(category=category).inc()


def increment_recommendation_feedback(*, feedback: str) -> None:
    recommendation_feedback.labels(feedback=feedback).inc()


def add_llm_token_usage(tokens: int, *, model: str, direction: str) -> None:
    llm_token_usage.labels(model=model, direction=direction).inc(tokens)


def add_llm_cost(cost: float, *, model: str) -> None:
    llm_cost.labels(model=model).inc(cost)


def observe_briefing_latency(seconds: float) -> None:
    briefing_latency.observe(seconds)


def observe_qa_latency(seconds: float) -> None:
    qa_latency.observe(seconds)


def _with_labels(metric: Any, labels: dict[str, str]) -> Any:
    if labels:
        return metric.labels(**labels)
    return metric
