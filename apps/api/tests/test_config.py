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
        EXPORT_STORAGE_DIR="/tmp/baseline-test-exports",
        _env_file=None,
    )

    assert settings.api_auth_token == "test-token-with-at-least-32-characters"
    assert str(settings.export_storage_dir) == "/tmp/baseline-test-exports"


def test_production_settings_require_export_storage_dir() -> None:
    with pytest.raises(ValidationError, match="EXPORT_STORAGE_DIR"):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            BASELINE_API_AUTH_TOKEN="test-token-with-at-least-32-characters",
            _env_file=None,
        )


def test_production_settings_reject_weak_api_auth_token() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            BASELINE_API_AUTH_TOKEN="short-token",
            EXPORT_STORAGE_DIR="/tmp/baseline-test-exports",
            _env_file=None,
        )


def test_http_embedding_provider_requires_complete_configuration() -> None:
    with pytest.raises(ValidationError, match="KNOWLEDGE_EMBEDDING_API_URL"):
        Settings(
            APP_ENV="test",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            KNOWLEDGE_EMBEDDING_PROVIDER="http",
            _env_file=None,
        )


def test_production_settings_reject_http_deepseek_url() -> None:
    with pytest.raises(ValidationError, match="DEEPSEEK_API_URL"):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            BASELINE_API_AUTH_TOKEN="test-token-with-at-least-32-characters",
            EXPORT_STORAGE_DIR="/tmp/baseline-test-exports",
            DEEPSEEK_API_URL="http://api.deepseek.test/chat/completions",
            _env_file=None,
        )


def test_staging_settings_reject_http_deepseek_url() -> None:
    with pytest.raises(ValidationError, match="DEEPSEEK_API_URL"):
        Settings(
            APP_ENV="staging",
            DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
            REDIS_URL="redis://localhost:6379/0",
            BASELINE_API_AUTH_TOKEN="test-token-with-at-least-32-characters",
            EXPORT_STORAGE_DIR="/tmp/baseline-test-exports",
            DEEPSEEK_API_URL="http://api.deepseek.test/chat/completions",
            _env_file=None,
        )


def test_test_settings_allow_http_deepseek_url() -> None:
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
        DEEPSEEK_API_URL="http://api.deepseek.test/chat/completions",
        _env_file=None,
    )
    assert settings.deepseek_api_url == "http://api.deepseek.test/chat/completions"
