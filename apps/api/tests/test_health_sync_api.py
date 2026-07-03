"""Tests for P1-01 health sync ingestion."""

from __future__ import annotations

import datetime as dt
from collections.abc import Generator
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from baseline_api.api.v1.health import get_normalization_queue
from baseline_api.app import create_app
from baseline_api.config import Settings
from baseline_api.db.models.enums import PrivacyMode
from baseline_api.db.models.ingestion import HealthImportBatch, RawHealthSample
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.db.repositories.ingestion import RawHealthSampleRepository
from baseline_api.db.session import get_db_session
from baseline_api.ingestion import sync_service as sync_service_module


class FakeNormalizationQueue:
    def __init__(
        self,
        visibility_engine: Engine | None = None,
        failures_before_success: int = 0,
    ) -> None:
        self.enqueued: list[tuple[UUID, UUID]] = []
        self.visible_raw_counts: list[int] = []
        self._visibility_engine = visibility_engine
        self._failures_before_success = failures_before_success

    async def enqueue_batch(self, *, import_batch_id: UUID, user_id: UUID) -> str:
        if self._failures_before_success:
            self._failures_before_success -= 1
            raise RuntimeError("queue unavailable")
        if self._visibility_engine is not None:
            with Session(self._visibility_engine) as session:
                rows = session.exec(
                    select(RawHealthSample).where(
                        RawHealthSample.import_batch_id == import_batch_id
                    )
                ).all()
                self.visible_raw_counts.append(len(rows))
        self.enqueued.append((import_batch_id, user_id))
        return f"job:{import_batch_id}"


def _settings(
    database_url: str = "postgresql+psycopg://baseline@localhost:5432/baseline",
) -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL=database_url,
        REDIS_URL="redis://localhost:6379/0",
    )


def _client(
    db_session: Session,
    queue: FakeNormalizationQueue,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_normalization_queue] = lambda: queue
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _real_db_client(
    db_engine: Engine,
    queue: FakeNormalizationQueue,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    app = create_app(_settings(str(db_engine.url)))
    app.dependency_overrides[get_normalization_queue] = lambda: queue
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _seed_user_with_consent(
    db_session: Session,
    *,
    categories: list[str] | None = None,
    revoked_at: dt.datetime | None = None,
) -> User:
    user = User(privacy_mode=PrivacyMode.local_only, active_consent_version="v1")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=categories or ["all"],
            timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            revoked_at=revoked_at,
        )
    )
    db_session.flush()
    return user


def _seed_committed_user_with_consent(db_engine: Engine) -> UUID:
    with Session(db_engine) as session:
        user = _seed_user_with_consent(session)
        user_id = user.id
        session.commit()
        return user_id


def _clear_committed_rows(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        connection.execute(text('TRUNCATE TABLE "user" CASCADE'))


def _sample(
    source_sample_id: str,
    *,
    sample_type: str = "heart_rate_variability",
    value: float = 52.4,
    end_before_start: bool = False,
) -> dict[str, object]:
    start = dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC)
    end = start - dt.timedelta(minutes=5) if end_before_start else start + dt.timedelta(minutes=5)
    return {
        "source_sample_id": source_sample_id,
        "sample_type": sample_type,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "value": value,
        "unit": "ms" if sample_type == "heart_rate_variability" else "count",
        "source_metadata": {"synthetic": True, "source": "unit-test"},
    }


def _payload(client_sync_id: str, samples: list[dict[str, object]]) -> dict[str, object]:
    return {
        "client_sync_id": client_sync_id,
        "device_id": "test-watch",
        "timezone": "UTC",
        "samples": samples,
        "last_anchor": "anchor-previous",
        "consent_version": "v1",
    }


def _raw_rows(db_session: Session) -> list[RawHealthSample]:
    return list(db_session.exec(select(RawHealthSample)).all())


def _batch_rows(db_session: Session) -> list[HealthImportBatch]:
    return list(db_session.exec(select(HealthImportBatch)).all())


def test_replaying_identical_batch_reports_duplicates_without_new_rows(
    db_session,
    monkeypatch,
) -> None:
    user = _seed_user_with_consent(db_session)
    queue = FakeNormalizationQueue()
    client = _client(db_session, queue)
    payload = _payload("sync-replay", [_sample("hk-1"), _sample("hk-2", value=53.1)])
    metric_calls: list[dict[str, float | int]] = []
    log_calls: list[dict[str, object]] = []

    def record_metrics(**kwargs: float | int) -> None:
        metric_calls.append(kwargs)

    def log_event(*args: object, **kwargs: object) -> None:
        log_calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(sync_service_module, "_record_metrics", record_metrics)
    monkeypatch.setattr(sync_service_module, "log_event", log_event)

    first = client.post("/v1/health/sync", json=payload)
    replay = client.post("/v1/health/sync", json=payload)

    assert first.status_code == 200
    assert replay.status_code == 200
    first_data = first.json()["data"]
    replay_data = replay.json()["data"]
    assert first_data["accepted_count"] == 2
    assert replay_data["sync_id"] == first_data["sync_id"]
    assert replay_data["accepted_count"] == 0
    assert replay_data["duplicate_count"] == 2
    assert replay_data["next_anchor"] == first_data["next_anchor"]
    assert len(_raw_rows(db_session)) == 2
    assert len(_batch_rows(db_session)) == 1
    assert queue.enqueued == [(UUID(first_data["sync_id"]), user.id)]
    assert metric_calls[-1]["accepted_count"] == 0
    assert metric_calls[-1]["duplicate_count"] == 2
    assert log_calls[-1]["kwargs"]["metadata"] == {
        "accepted_count": 0,
        "duplicate_count": 2,
        "rejected_count": 0,
    }


def test_duplicate_samples_in_same_batch_are_counted_without_unique_error(db_session) -> None:
    user = _seed_user_with_consent(db_session)
    queue = FakeNormalizationQueue()
    client = _client(db_session, queue)
    sample = _sample("hk-same-request")

    response = client.post("/v1/health/sync", json=_payload("sync-same-batch", [sample, sample]))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["accepted_count"] == 1
    assert data["duplicate_count"] == 1
    assert data["rejected_count"] == 0
    rows = _raw_rows(db_session)
    assert len(rows) == 1
    assert rows[0].source_sample_id == "hk-same-request"
    assert queue.enqueued == [(UUID(data["sync_id"]), user.id)]


def test_raw_sample_integrity_race_retries_as_duplicate(db_engine, monkeypatch) -> None:
    _clear_committed_rows(db_engine)
    try:
        _seed_committed_user_with_consent(db_engine)
        queue = FakeNormalizationQueue()
        client = _real_db_client(db_engine, queue)
        original_create = RawHealthSampleRepository.create
        inserted_conflict = False

        def create_with_concurrent_conflict(
            repository: RawHealthSampleRepository,
            sample: RawHealthSample,
        ) -> RawHealthSample:
            nonlocal inserted_conflict
            if not inserted_conflict:
                inserted_conflict = True
                with Session(db_engine) as session:
                    session.add(
                        RawHealthSample(
                            user_id=sample.user_id,
                            source_platform=sample.source_platform,
                            source_device=sample.source_device,
                            source_sample_id=sample.source_sample_id,
                            content_hash=sample.content_hash,
                            sample_type=sample.sample_type,
                            start_time=sample.start_time,
                            end_time=sample.end_time,
                            raw_value=sample.raw_value,
                            raw_unit=sample.raw_unit,
                            source_metadata=sample.source_metadata,
                            imported_at=sample.imported_at,
                            import_batch_id=uuid4(),
                        )
                    )
                    session.commit()
            return original_create(repository, sample)

        monkeypatch.setattr(RawHealthSampleRepository, "create", create_with_concurrent_conflict)

        response = client.post(
            "/v1/health/sync",
            json=_payload("sync-integrity-race", [_sample("hk-race")]),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["accepted_count"] == 0
        assert data["duplicate_count"] == 1
        assert queue.enqueued == []
        with Session(db_engine) as session:
            batches = _batch_rows(session)
            assert len(batches) == 1
            assert batches[0].accepted_count == 0
            assert batches[0].duplicate_count == 1
            rows = _raw_rows(session)
            assert len(rows) == 1
            assert rows[0].source_sample_id == "hk-race"
    finally:
        _clear_committed_rows(db_engine)


def test_mixed_batch_accepts_new_samples_and_counts_known_duplicates(db_session) -> None:
    _seed_user_with_consent(db_session)
    queue = FakeNormalizationQueue()
    client = _client(db_session, queue)

    first = client.post("/v1/health/sync", json=_payload("sync-one", [_sample("hk-known")]))
    mixed = client.post(
        "/v1/health/sync",
        json=_payload("sync-two", [_sample("hk-known"), _sample("hk-new", value=50.0)]),
    )

    assert first.status_code == 200
    assert mixed.status_code == 200
    mixed_data = mixed.json()["data"]
    assert mixed_data["accepted_count"] == 1
    assert mixed_data["duplicate_count"] == 1
    assert mixed_data["rejected_count"] == 0
    assert len(_raw_rows(db_session)) == 2
    assert len(queue.enqueued) == 2


def test_malformed_samples_are_rejected_without_blocking_valid_samples(db_session) -> None:
    _seed_user_with_consent(db_session)
    queue = FakeNormalizationQueue()
    client = _client(db_session, queue)

    response = client.post(
        "/v1/health/sync",
        json=_payload(
            "sync-malformed",
            [_sample("hk-good"), _sample("hk-bad", end_before_start=True)],
        ),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["accepted_count"] == 1
    assert data["duplicate_count"] == 0
    assert data["rejected_count"] == 1
    assert data["warnings"] == ["1 malformed sample(s) were rejected."]
    assert data["data_quality_summary"]["status"] == "degraded"
    rows = _raw_rows(db_session)
    assert len(rows) == 1
    assert rows[0].source_sample_id == "hk-good"


def test_consent_gate_rejects_request_and_persists_nothing(db_session) -> None:
    _seed_user_with_consent(db_session, categories=["sleep"])
    queue = FakeNormalizationQueue()
    client = _client(db_session, queue)

    response = client.post("/v1/health/sync", json=_payload("sync-no-consent", [_sample("hk-1")]))

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "consent_category_disabled"
    assert _raw_rows(db_session) == []
    assert _batch_rows(db_session) == []
    assert queue.enqueued == []


def test_missing_consent_version_returns_typed_error_without_persisting(db_session) -> None:
    _seed_user_with_consent(db_session)
    queue = FakeNormalizationQueue()
    client = _client(db_session, queue)
    payload = _payload("sync-missing-consent", [_sample("hk-1")])
    del payload["consent_version"]

    response = client.post("/v1/health/sync", json=payload)

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "consent_invalid"
    assert _raw_rows(db_session) == []
    assert _batch_rows(db_session) == []
    assert queue.enqueued == []


def test_invalid_consent_version_returns_typed_error_without_persisting(db_session) -> None:
    _seed_user_with_consent(db_session)
    queue = FakeNormalizationQueue()
    client = _client(db_session, queue)
    payload = _payload("sync-invalid-consent", [_sample("hk-1")])
    payload["consent_version"] = ""

    response = client.post("/v1/health/sync", json=payload)

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "consent_invalid"
    assert _raw_rows(db_session) == []
    assert _batch_rows(db_session) == []
    assert queue.enqueued == []


def test_openapi_documents_health_sync_typed_error_responses() -> None:
    schema = create_app(_settings()).openapi()
    responses = schema["paths"]["/v1/health/sync"]["post"]["responses"]

    for status_code in ("403", "409", "503"):
        assert responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/APIEnvelope_NoneType_"
        }


def test_sync_persists_raw_provenance_and_enqueues_normalization_job(db_session) -> None:
    user = _seed_user_with_consent(db_session)
    queue = FakeNormalizationQueue()
    client = _client(db_session, queue)

    response = client.post(
        "/v1/health/sync",
        json=_payload("sync-integration", [_sample("hk-123")]),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["accepted_count"] == 1
    assert data["next_anchor"].startswith(f"health-sync:{data['sync_id']}:")
    assert data["data_quality_summary"]["status"] == "ok"

    rows = _raw_rows(db_session)
    assert len(rows) == 1
    raw = rows[0]
    assert raw.user_id == user.id
    assert raw.source_platform == "apple_health"
    assert raw.source_device == "test-watch"
    assert raw.source_sample_id == "hk-123"
    assert raw.content_hash
    assert str(raw.import_batch_id) == data["sync_id"]
    assert raw.raw_unit == "ms"
    assert raw.source_metadata == {"synthetic": True, "source": "unit-test"}
    assert queue.enqueued == [(UUID(data["sync_id"]), user.id)]


def test_normalization_job_is_enqueued_after_raw_rows_commit(db_engine) -> None:
    _clear_committed_rows(db_engine)
    try:
        user_id = _seed_committed_user_with_consent(db_engine)
        queue = FakeNormalizationQueue(visibility_engine=db_engine)
        client = _real_db_client(db_engine, queue)

        response = client.post(
            "/v1/health/sync",
            json=_payload("sync-post-commit-enqueue", [_sample("hk-visible")]),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        sync_id = UUID(data["sync_id"])
        assert queue.enqueued == [(sync_id, user_id)]
        assert queue.visible_raw_counts == [1]

        with Session(db_engine) as session:
            batch = session.get(HealthImportBatch, sync_id)
            assert batch is not None
            assert batch.normalization_job_id == f"job:{sync_id}"
    finally:
        _clear_committed_rows(db_engine)


def test_replay_repairs_missing_normalization_job_after_enqueue_failure(db_engine) -> None:
    _clear_committed_rows(db_engine)
    try:
        user_id = _seed_committed_user_with_consent(db_engine)
        queue = FakeNormalizationQueue(
            visibility_engine=db_engine,
            failures_before_success=1,
        )
        client = _real_db_client(db_engine, queue, raise_server_exceptions=False)
        payload = _payload("sync-retry-enqueue", [_sample("hk-retry-enqueue")])

        failed = client.post("/v1/health/sync", json=payload)

        assert failed.status_code == 503
        failed_body = failed.json()
        assert failed_body["status"] == "error"
        assert failed_body["error"]["code"] == "normalization_enqueue_failed"
        with Session(db_engine) as session:
            batch = session.exec(select(HealthImportBatch)).one()
            sync_id = batch.id
            assert batch.normalization_job_id is None
            assert len(_raw_rows(session)) == 1

        replay = client.post("/v1/health/sync", json=payload)

        assert replay.status_code == 200
        data = replay.json()["data"]
        assert UUID(data["sync_id"]) == sync_id
        assert data["accepted_count"] == 0
        assert data["duplicate_count"] == 1
        assert queue.enqueued == [(sync_id, user_id)]
        assert queue.visible_raw_counts == [1]
        with Session(db_engine) as session:
            batch = session.get(HealthImportBatch, sync_id)
            assert batch is not None
            assert batch.normalization_job_id == f"job:{sync_id}"
    finally:
        _clear_committed_rows(db_engine)
