import pytest
from pydantic import ValidationError

from baseline_api.config import Settings


def test_settings_missing_required_env_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValidationError, match="APP_ENV"):
        Settings(_env_file=None)
