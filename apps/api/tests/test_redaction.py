"""Tests for export key custody, key-store expiry, and single-user context."""

from __future__ import annotations

import base64
import datetime as dt
import json
from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from baseline_api.api.v1.health import get_normalization_queue
from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.db.models import User
from baseline_api.db.models.enums import PrivacyMode
from baseline_api.db.models.user import ConsentRecord
from baseline_api.db.session import get_db_session
from baseline_api.privacy import LocalExportStore, RedisExportKeyStore
from baseline_api.privacy.disclosure import ModelDisclosureService
from baseline_api.privacy.export import encrypt_bytes


class FakeNormalizationQueue:
    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str:
        return f"job:{import_batch_id}:{user_id}"


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )


def _client(db_session: Session) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_normalization_queue] = lambda: FakeNormalizationQueue()
    return TestClient(app)


def _consent_payload() -> dict[str, object]:
    return {
        "consent_version": "redaction-v1",
        "health_categories_enabled": ["all"],
        "cloud_processing_enabled": True,
        "external_llm_enabled": False,
        "raw_note_processing_enabled": False,
        "privacy_mode": "hybrid",
    }


def test_export_manifest_does_not_contain_encryption_key(
    db_session: Session,
    tmp_path,
) -> None:
    user = User(privacy_mode=PrivacyMode.hybrid, active_consent_version="v1")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=["all"],
            cloud_processing_enabled=True,
            external_llm_enabled=False,
            raw_note_processing_enabled=False,
            timestamp=dt.datetime.now(dt.UTC),
        )
    )
    db_session.flush()

    export_store = LocalExportStore(tmp_path)
    client = _client(db_session)
    client.app.state.export_store = export_store

    response = client.post(
        "/v1/data/export",
        json={
            "export_scope": "consent",
            "format": "json",
            "include_raw_data": False,
            "include_model_traces": False,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    key_base64 = data["encryption"]["key_base64"]

    stored = export_store.get(UUID(data["export_job_id"]))
    manifest = json.loads((tmp_path / f"{stored.job_id}.export.json").read_text())
    rendered_manifest = json.dumps(manifest, sort_keys=True)
    assert key_base64 not in rendered_manifest
    assert "key" not in manifest


def test_redis_export_key_store_returns_key_before_expiry_and_none_after() -> None:
    try:
        store = RedisExportKeyStore("redis://localhost:6379/0", prefix="baseline:test:export:key:")
        store._client.ping()
    except Exception:
        pytest.skip("Redis is not available for key-store test")

    job_id = uuid4()
    key = b"0" * 32
    store.delete_key(job_id)

    store.store_key(job_id, key, ttl_seconds=1)
    assert store.get_key(job_id) == key

    # Wait for the key to expire.
    import time

    time.sleep(1.1)
    assert store.get_key(job_id) is None


def test_model_disclosure_output_does_not_contain_export_key(db_session: Session) -> None:
    user = User(privacy_mode=PrivacyMode.hybrid, active_consent_version="v1")
    db_session.add(user)
    db_session.flush()

    key = b"0" * 32
    encrypted = encrypt_bytes(b"export payload", key)
    response = ModelDisclosureService(db_session).list_model_payloads(user=user)
    rendered = response.model_dump_json()

    assert base64.b64encode(key).decode("ascii") not in rendered
    assert encrypted.hex() not in rendered


def test_local_export_store_rejects_temp_backing_in_production(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="Temp-backed export storage is not allowed"):
        LocalExportStore(None, retention_hours=1, app_env="production")

    with pytest.raises(RuntimeError, match="Temp-backed export storage is not allowed"):
        LocalExportStore(None, retention_hours=1, app_env="staging")

    # A configured directory is allowed in production.
    store = LocalExportStore(tmp_path, retention_hours=1, app_env="production")
    assert store._root == tmp_path


def test_single_user_context_resolves_same_user_across_endpoints(db_session: Session) -> None:
    client = _client(db_session)

    consent = client.post("/v1/data/consent", json=_consent_payload())
    assert consent.status_code == 200
    user_id = db_session.exec(select(User)).one().id

    sync = client.post(
        "/v1/health/sync",
        json={
            "client_sync_id": "single-user-sync",
            "device_id": "watch",
            "timezone": "UTC",
            "samples": [
                {
                    "source_sample_id": "hrv-1",
                    "sample_type": "heart_rate_variability",
                    "start_time": "2026-07-04T07:00:00Z",
                    "value": 50.0,
                    "unit": "ms",
                }
            ],
            "consent_version": "redaction-v1",
        },
    )
    assert sync.status_code == 200

    checkin = client.post(
        "/v1/checkins/daily",
        json={
            "date": "2026-07-04",
            "energy_score": 6,
            "sensitive_note_policy": "exclude_from_external_llm",
        },
    )
    assert checkin.status_code == 200

    goal = client.post(
        "/v1/goals",
        json={
            "category": "strength",
            "priority": 5,
            "time_horizon": "long_term",
            "success_metric": "consistency",
            "constraints": {},
        },
    )
    assert goal.status_code == 200

    assistant = client.post(
        "/v1/assistant/query",
        json={
            "question": "How has my sleep looked recently?",
            "date_context": "2026-07-04",
            "allowed_data_scope": ["recent_health"],
            "include_external_knowledge": False,
            "privacy_mode": "hybrid",
        },
    )
    assert assistant.status_code == 200

    users = list(db_session.exec(select(User)).all())
    assert len(users) == 1
    assert users[0].id == user_id
