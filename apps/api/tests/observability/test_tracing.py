from uuid import UUID

from fastapi.testclient import TestClient

from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.observability.tracing import TRACE_HEADER, create_job_context, get_trace_context


def test_trace_id_is_returned_and_propagates_to_job_context() -> None:
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )
    app = create_app(settings)

    @app.get("/trace-test")
    async def trace_test() -> dict[str, str | None]:
        active_context = get_trace_context()
        job_context = create_job_context(job_id="daily-pipeline")
        return {
            "trace_id": active_context.trace_id,
            "job_trace_id": job_context.trace_id,
            "job_id": job_context.job_id,
        }

    client = TestClient(app)

    response = client.get("/trace-test")

    assert response.status_code == 200
    returned_trace_id = response.headers[TRACE_HEADER]
    UUID(returned_trace_id)
    assert response.json() == {
        "trace_id": returned_trace_id,
        "job_trace_id": returned_trace_id,
        "job_id": "daily-pipeline",
    }
