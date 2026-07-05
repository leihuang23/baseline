from fastapi.testclient import TestClient

from baseline_api.app import create_app
from baseline_api.config import Settings


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
        BASELINE_API_AUTH_TOKEN="secret-token",
    )


def _client() -> TestClient:
    app = create_app(_settings())

    @app.get("/protected-test")
    def protected_test() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_api_token_protects_non_public_routes() -> None:
    client = _client()

    response = client.get("/protected-test")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_api_token_accepts_valid_bearer_token() -> None:
    client = _client()

    response = client.get(
        "/protected-test",
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_token_accepts_valid_baseline_api_key_header() -> None:
    client = _client()

    response = client.get(
        "/protected-test",
        headers={"X-Baseline-API-Key": "secret-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_token_rejects_invalid_token() -> None:
    client = _client()

    response = client.get(
        "/protected-test",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_api_token_rejection_includes_trace_header() -> None:
    client = _client()

    response = client.get("/protected-test")

    assert response.status_code == 401
    assert response.headers["X-Trace-Id"]


def test_api_token_allows_public_health_probe() -> None:
    client = _client()

    response = client.get("/health")

    assert response.status_code == 200


def test_api_token_allows_public_docs_and_openapi() -> None:
    client = _client()

    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200
