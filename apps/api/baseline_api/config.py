from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        validate_default=True,
    )

    app_name: str = "Baseline"
    app_env: Literal["local", "test", "staging", "production"] = Field(alias="APP_ENV")
    database_url: PostgresDsn = Field(alias="DATABASE_URL")
    redis_url: RedisDsn = Field(alias="REDIS_URL")
    api_auth_token: str | None = Field(default=None, alias="BASELINE_API_AUTH_TOKEN")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )
    llm_default_provider: str = Field(default="deepseek", alias="LLM_DEFAULT_PROVIDER")
    llm_cheap_model: str = Field(default="deepseek-v4-pro", alias="LLM_CHEAP_MODEL")
    llm_strong_model: str = Field(default="deepseek-v4-pro", alias="LLM_STRONG_MODEL")
    llm_fallback_model: str = Field(
        default="baseline-local-deterministic-v1",
        alias="LLM_FALLBACK_MODEL",
    )
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_api_url: str = Field(
        default="https://api.deepseek.com/chat/completions",
        alias="DEEPSEEK_API_URL",
    )
    daily_briefing_cost_budget: float = Field(
        default=1.0,
        ge=0,
        alias="DAILY_BRIEFING_COST_BUDGET",
    )
    model_provider_failure_alert_threshold: int = Field(
        default=3,
        ge=1,
        alias="MODEL_PROVIDER_FAILURE_ALERT_THRESHOLD",
    )
    schema_validation_failure_alert_threshold: int = Field(
        default=3,
        ge=1,
        alias="SCHEMA_VALIDATION_FAILURE_ALERT_THRESHOLD",
    )
    daily_briefing_failure_alert_threshold: int = Field(
        default=1,
        ge=1,
        alias="DAILY_BRIEFING_FAILURE_ALERT_THRESHOLD",
    )
    sync_failure_alert_threshold: int = Field(
        default=3,
        ge=1,
        alias="SYNC_FAILURE_ALERT_THRESHOLD",
    )
    deletion_failure_alert_threshold: int = Field(
        default=1,
        ge=1,
        alias="DELETION_FAILURE_ALERT_THRESHOLD",
    )

    @model_validator(mode="after")
    def require_production_auth_token(self) -> "Settings":
        token = self.api_auth_token.strip() if self.api_auth_token else None
        self.api_auth_token = token
        if self.app_env in {"staging", "production"}:
            if not token:
                raise ValueError(
                    "BASELINE_API_AUTH_TOKEN is required when APP_ENV is staging or production."
                )
            if len(token) < 32:
                raise ValueError(
                    "BASELINE_API_AUTH_TOKEN must be at least 32 characters "
                    "when APP_ENV is staging or production."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
