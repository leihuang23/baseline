import pytest
from pydantic import ValidationError

from baseline_api.config import Settings


def test_settings_missing_required_env_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValidationError, match="APP_ENV"):
        Settings(_env_file=None)


def test_production_settings_require_api_auth_token() -> None:
    with pytest.raises(ValidationError, match="BASELINE_API_AUTH_TOKEN"):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            _env_file=None,
        )


def test_staging_settings_require_api_auth_token() -> None:
    with pytest.raises(ValidationError, match="BASELINE_API_AUTH_TOKEN"):
        Settings(
            APP_ENV="staging",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            _env_file=None,
        )


def test_production_settings_accept_api_auth_token() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
        BASELINE_API_AUTH_TOKEN="test-token-with-at-least-32-characters",
        _env_file=None,
    )

    assert settings.api_auth_token == "test-token-with-at-least-32-characters"


def test_production_settings_reject_weak_api_auth_token() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            BASELINE_API_AUTH_TOKEN="short-token",
            _env_file=None,
        )
