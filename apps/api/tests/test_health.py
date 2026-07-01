from fastapi.testclient import TestClient

from baseline_api.app import create_app
from baseline_api.config import Settings


def test_health_endpoints_return_ok() -> None:
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5432/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )
    client = TestClient(create_app(settings))

    health_response = client.get("/health")
    ping_response = client.get("/v1/health/ping")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "service": "Baseline"}
    assert ping_response.status_code == 200
    assert ping_response.json() == {"status": "ok"}
