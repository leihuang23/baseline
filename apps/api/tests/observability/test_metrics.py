import pytest
from fastapi.testclient import TestClient

from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.observability import metrics


def test_metrics_endpoint_exposes_registry() -> None:
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5432/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )
    client = TestClient(create_app(settings))

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "baseline_sync_success_total" in response.text


def test_metric_helpers_cover_prd_metric_list() -> None:
    metrics.increment_sync_success(source="apple_health")
    metrics.observe_sync_latency(0.1, source="apple_health")
    metrics.observe_backfill_duration(0.2, source="apple_health")
    metrics.set_duplicate_sample_rate(0.0, source="apple_health")
    metrics.increment_rejected_sample_count(reason="schema")
    metrics.set_data_completeness_by_day(0.99, day="2026-07-03")
    metrics.set_data_staleness_flag(False, day="2026-07-03", sample_type="sleep_duration")
    metrics.increment_feature_job_result(status="success")
    metrics.increment_llm_generation_result(status="blocked")
    metrics.increment_schema_validation_failure(schema_name="briefing")
    metrics.increment_safety_block(category="medical")
    metrics.increment_recommendation_feedback(feedback="useful")
    metrics.add_llm_token_usage(12, model="test-model", direction="input")
    metrics.add_llm_cost(0.01, model="test-model")
    metrics.observe_briefing_latency(0.3)
    metrics.observe_qa_latency(0.4)

    assert "baseline_llm_generation_total" in metrics.generate_latest(metrics.registry).decode()


def test_metric_decorators_count_and_time_calls() -> None:
    @metrics.count_calls(metrics.sync_success, source="decorator")
    @metrics.time_calls(metrics.sync_latency, source="decorator")
    def decorated() -> str:
        return "ok"

    assert decorated() == "ok"


@pytest.mark.asyncio
async def test_async_metric_decorator_times_calls() -> None:
    @metrics.time_async_calls(metrics.qa_latency)
    async def decorated() -> str:
        return "ok"

    assert await decorated() == "ok"
