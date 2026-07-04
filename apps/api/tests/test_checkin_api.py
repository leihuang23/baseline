"""Tests for P2-01 daily check-in API + redaction."""

from __future__ import annotations

import datetime as dt
from collections.abc import Generator
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from baseline_api.api.checkins import get_analysis_queue, get_redaction_service
from baseline_api.app import create_app
from baseline_api.checkin import (
    AnalysisJobQueue,
    NoteRedactionService,
    RedactionResult,
    StubNoteRedactionService,
)
from baseline_api.checkin import service as service_module
from baseline_api.config import Settings
from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.enums import (
    AuditEventType,
    SensitiveNotePolicy,
)
from baseline_api.db.models.enums import (
    RedactionStatus as ModelRedactionStatus,
)
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.db.session import get_db_session
from baseline_api.observability import logging as logging_module
from baseline_api.schemas.enums import RedactionStatus


class FakeAnalysisQueue:
    def __init__(self, job_id: UUID | None = None) -> None:
        self.enqueued: list[dict[str, Any]] = []
        self._job_id = job_id or uuid4()

    async def enqueue_daily_analysis(
        self,
        *,
        checkin_id: UUID,
        user_id: UUID,
        date: dt.date,
    ) -> UUID | None:
        self.enqueued.append(
            {
                "checkin_id": checkin_id,
                "user_id": user_id,
                "date": date,
            }
        )
        return self._job_id


class FailingAnalysisQueue:
    async def enqueue_daily_analysis(
        self,
        *,
        checkin_id: UUID,
        user_id: UUID,
        date: dt.date,
    ) -> UUID | None:
        raise RuntimeError("queue unavailable")


class FakeRedactionService:
    """Records inputs and returns deterministic, non-echoing references."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, SensitiveNotePolicy]] = []

    async def redact(
        self,
        note: str | None,
        policy: SensitiveNotePolicy,
    ) -> RedactionResult:
        self.calls.append((note, policy))
        if not note:
            return RedactionResult(
                reference=None,
                summary=None,
                status=RedactionStatus.none,
            )
        if policy == SensitiveNotePolicy.exclude_from_external_llm:
            return RedactionResult(
                reference="ref:exclude:abc123",
                summary=None,
                status=RedactionStatus.redacted,
            )
        if policy == SensitiveNotePolicy.summarize_before_external_llm:
            return RedactionResult(
                reference="ref:summarize:abc123",
                summary="Summarized note.",
                status=RedactionStatus.partial,
            )
        return RedactionResult(
            reference="ref:allow:abc123",
            summary=note,
            status=RedactionStatus.none,
        )


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://baseline@localhost:5433/baseline",
        REDIS_URL="redis://localhost:6379/0",
    )


def _client(
    db_session: Session,
    *,
    redaction: NoteRedactionService | None = None,
    queue: AnalysisJobQueue | None = None,
) -> TestClient:
    app = create_app(_settings())

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_redaction_service] = lambda: (
        redaction or StubNoteRedactionService()
    )
    app.dependency_overrides[get_analysis_queue] = lambda: queue or FakeAnalysisQueue()
    return TestClient(app)


def _seed_user_with_consent(
    db_session: Session,
    *,
    external_llm_enabled: bool = False,
    raw_note_processing_enabled: bool = False,
) -> User:
    user = User(
        privacy_mode="local_only",
        active_consent_version="v1",
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=["all"],
            cloud_processing_enabled=False,
            external_llm_enabled=external_llm_enabled,
            raw_note_processing_enabled=raw_note_processing_enabled,
            timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        )
    )
    db_session.flush()
    return user


def _payload(
    *,
    date: str = "2026-01-15",
    energy_score: int | None = 7,
    mood_score: int | None = None,
    soreness_score: int | None = None,
    stress_score: int | None = None,
    perceived_recovery_score: int | None = None,
    food_quality_score: int | None = None,
    flags: dict[str, Any] | None = None,
    structured_notes: dict[str, Any] | None = None,
    free_text_note: str | None = None,
    sensitive_note_policy: str = "exclude_from_external_llm",
) -> dict[str, Any]:
    return {
        "date": date,
        "energy_score": energy_score,
        "mood_score": mood_score,
        "soreness_score": soreness_score,
        "stress_score": stress_score,
        "perceived_recovery_score": perceived_recovery_score,
        "food_quality_score": food_quality_score,
        "flags": flags or {},
        "structured_notes": structured_notes or {},
        "free_text_note": free_text_note,
        "sensitive_note_policy": sensitive_note_policy,
    }


def _checkin_rows(db_session: Session) -> list[DailyCheckIn]:
    return list(db_session.exec(select(DailyCheckIn)).all())


def _audit_events(db_session: Session) -> list[AuditEvent]:
    return list(db_session.exec(select(AuditEvent).order_by(col(AuditEvent.timestamp))).all())


def test_partial_checkin_is_accepted(db_session: Session) -> None:
    user = _seed_user_with_consent(db_session)
    queue = FakeAnalysisQueue()
    client = _client(db_session, queue=queue)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=None,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["accepted_fields"] == []
    assert data["redaction_status"] == "none"
    rows = _checkin_rows(db_session)
    assert len(rows) == 1
    assert rows[0].user_id == user.id
    assert rows[0].energy_score is None
    assert rows[0].redaction_status == ModelRedactionStatus.none
    assert queue.enqueued


def test_sensitive_lifestyle_fields_are_optional(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=6,
            flags={
                "alcohol": True,
                "caffeine_notes": "One espresso",
                "illness": False,
                "injury": False,
                "travel": True,
            },
            structured_notes={
                "sexual_health": "optional high-level indicator",
                "training": "lower body",
            },
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "structured_notes" in data["accepted_fields"]
    assert "alcohol_flag" in data["accepted_fields"]
    assert "caffeine_notes" in data["accepted_fields"]
    assert "travel_flag" in data["accepted_fields"]
    assert "illness_flag" not in data["accepted_fields"]
    rows = _checkin_rows(db_session)
    assert rows[0].structured_notes == {
        "sexual_health": "optional high-level indicator",
        "training": "lower body",
    }


def test_structured_notes_reject_free_text_blobs(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            structured_notes={"training": "x" * 81},
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 422
    assert _checkin_rows(db_session) == []


def test_caffeine_notes_reject_free_text_blobs(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            flags={"caffeine_notes": "x" * 81},
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 422
    assert _checkin_rows(db_session) == []


def test_redaction_excludes_raw_note_from_storage_queue_and_logs(
    db_session: Session,
    monkeypatch: Any,
) -> None:
    _seed_user_with_consent(db_session, external_llm_enabled=True)
    queue = FakeAnalysisQueue()
    client = _client(db_session, queue=queue)

    log_calls: list[dict[str, Any]] = []

    def capture_log(event_type: str, *, status: str, **kwargs: Any) -> None:
        log_calls.append({"event_type": event_type, "status": status, **kwargs})

    monkeypatch.setattr(service_module, "log_event", capture_log)
    monkeypatch.setattr(logging_module, "log_event", capture_log)

    raw_note = "I have a very specific symptom I do not want to share."
    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note=raw_note,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["redaction_status"] == "redacted"

    rows = _checkin_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.free_text_note_reference is not None
    assert row.free_text_note_summary is None
    assert row.redaction_status == ModelRedactionStatus.redacted
    assert raw_note not in row.free_text_note_reference
    assert raw_note not in str(row.free_text_note_reference)

    assert len(queue.enqueued) == 1
    assert raw_note not in str(queue.enqueued[0])

    audits = _audit_events(db_session)
    assert raw_note not in str(audits)

    assert log_calls
    for call in log_calls:
        assert raw_note not in str(call)


def test_redaction_summarizes_before_external_llm(db_session: Session) -> None:
    _seed_user_with_consent(db_session, external_llm_enabled=True)
    client = _client(db_session)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note="Felt flat after a poor night of sleep.",
            sensitive_note_policy="summarize_before_external_llm",
        ),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["redaction_status"] == "partial"
    rows = _checkin_rows(db_session)
    assert rows[0].free_text_note_reference is not None
    assert rows[0].free_text_note_summary == "User-provided note summarized locally."
    assert rows[0].redaction_status == ModelRedactionStatus.partial
    assert "Felt flat after a poor night of sleep." not in str(rows[0].free_text_note_summary)
    assert "summarize_before_external_llm" in rows[0].free_text_note_reference


def test_minimal_checkin_defaults_to_local_note_policy(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    response = client.post("/v1/checkins/daily", json={"date": "2026-01-15"})

    assert response.status_code == 200
    rows = _checkin_rows(db_session)
    assert len(rows) == 1
    assert rows[0].sensitive_note_policy == SensitiveNotePolicy.exclude_from_external_llm
    assert rows[0].redaction_status == ModelRedactionStatus.none


def test_edit_checkin_updates_fields_and_emits_audit(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    queue = FakeAnalysisQueue()
    client = _client(db_session, queue=queue)

    create = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )
    checkin_id = create.json()["data"]["checkin_id"]

    update = client.put(
        f"/v1/checkins/daily/{checkin_id}",
        json=_payload(
            energy_score=8,
            mood_score=7,
            soreness_score=2,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert update.status_code == 200
    data = update.json()["data"]
    assert data["checkin_id"] == checkin_id
    assert set(data["accepted_fields"]) == {
        "energy_score",
        "mood_score",
        "soreness_score",
    }

    rows = _checkin_rows(db_session)
    assert len(rows) == 1
    assert rows[0].energy_score == 8
    assert rows[0].mood_score == 7
    assert rows[0].soreness_score == 2

    audits = _audit_events(db_session)
    assert [a.event_type for a in audits] == [
        AuditEventType.checkin_submitted,
        AuditEventType.checkin_updated,
    ]


def test_get_checkin_by_date_returns_editable_payload_without_raw_note(
    db_session: Session,
) -> None:
    _seed_user_with_consent(db_session, external_llm_enabled=True)
    client = _client(db_session)

    create = client.post(
        "/v1/checkins/daily",
        json=_payload(
            date="2026-01-15",
            energy_score=5,
            mood_score=6,
            flags={"alcohol": True, "travel": True},
            structured_notes={"private_lifestyle_indicator": True},
            free_text_note="Private morning note",
            sensitive_note_policy="summarize_before_external_llm",
        ),
    )
    checkin_id = create.json()["data"]["checkin_id"]

    response = client.get("/v1/checkins/daily/by-date/2026-01-15")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["checkin_id"] == checkin_id
    assert data["request"]["date"] == "2026-01-15"
    assert data["request"]["energy_score"] == 5
    assert data["request"]["mood_score"] == 6
    assert data["request"]["flags"]["alcohol"] is True
    assert data["request"]["flags"]["travel"] is True
    assert data["request"]["structured_notes"] == {"private_lifestyle_indicator": True}
    assert data["request"]["free_text_note"] is None
    assert data["request"]["sensitive_note_policy"] == "summarize_before_external_llm"
    assert data["has_free_text_note"] is True


def test_update_from_loaded_checkin_preserves_hidden_note_metadata(
    db_session: Session,
) -> None:
    _seed_user_with_consent(db_session, external_llm_enabled=True)
    client = _client(db_session)

    create = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note="Private morning note",
            sensitive_note_policy="summarize_before_external_llm",
        ),
    )
    assert create.status_code == 200
    checkin_id = create.json()["data"]["checkin_id"]
    row = _checkin_rows(db_session)[0]
    original_reference = row.free_text_note_reference
    original_summary = row.free_text_note_summary
    assert original_reference is not None
    assert original_summary is not None

    loaded = client.get("/v1/checkins/daily/by-date/2026-01-15")
    assert loaded.status_code == 200
    update_payload = loaded.json()["data"]["request"]
    update_payload.pop("free_text_note")
    update_payload["energy_score"] = 8

    update = client.put(f"/v1/checkins/daily/{checkin_id}", json=update_payload)

    assert update.status_code == 200
    updated_row = _checkin_rows(db_session)[0]
    assert updated_row.energy_score == 8
    assert updated_row.free_text_note_reference == original_reference
    assert updated_row.free_text_note_summary == original_summary
    assert updated_row.sensitive_note_policy == SensitiveNotePolicy.summarize_before_external_llm
    assert updated_row.redaction_status == ModelRedactionStatus.partial


def test_update_explicit_null_note_clears_hidden_note_metadata(
    db_session: Session,
) -> None:
    _seed_user_with_consent(db_session, external_llm_enabled=True)
    client = _client(db_session)

    create = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note="Private morning note",
            sensitive_note_policy="summarize_before_external_llm",
        ),
    )
    assert create.status_code == 200
    checkin_id = create.json()["data"]["checkin_id"]

    update_payload = _payload(
        energy_score=5,
        free_text_note=None,
        sensitive_note_policy="exclude_from_external_llm",
    )
    update = client.put(f"/v1/checkins/daily/{checkin_id}", json=update_payload)

    assert update.status_code == 200
    updated_row = _checkin_rows(db_session)[0]
    assert updated_row.free_text_note_reference is None
    assert updated_row.free_text_note_summary is None
    assert updated_row.sensitive_note_policy == SensitiveNotePolicy.exclude_from_external_llm
    assert updated_row.redaction_status == ModelRedactionStatus.none


def test_partial_update_preserves_omitted_saved_scores(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    create = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            mood_score=6,
            stress_score=4,
            flags={"travel": True},
            structured_notes={"private_lifestyle_indicator": True},
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )
    assert create.status_code == 200
    checkin_id = create.json()["data"]["checkin_id"]

    update = client.put(
        f"/v1/checkins/daily/{checkin_id}",
        json={
            "date": "2026-01-15",
            "sensitive_note_policy": "exclude_from_external_llm",
        },
    )

    assert update.status_code == 200
    updated_row = _checkin_rows(db_session)[0]
    assert updated_row.energy_score == 5
    assert updated_row.mood_score == 6
    assert updated_row.stress_score == 4
    assert updated_row.travel_flag is True
    assert updated_row.structured_notes == {"private_lifestyle_indicator": True}


def test_delete_checkin_removes_row_and_emits_audit(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    create = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )
    checkin_id = create.json()["data"]["checkin_id"]

    delete = client.delete(f"/v1/checkins/daily/{checkin_id}")

    assert delete.status_code == 204
    assert _checkin_rows(db_session) == []
    audits = _audit_events(db_session)
    assert [a.event_type for a in audits] == [
        AuditEventType.checkin_submitted,
        AuditEventType.checkin_deleted,
    ]


def test_analysis_job_id_is_persisted_and_returned(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    queue = FakeAnalysisQueue()
    client = _client(db_session, queue=queue)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["analysis_job_id"] == str(queue._job_id)

    rows = _checkin_rows(db_session)
    assert len(rows) == 1
    assert rows[0].analysis_job_id == queue._job_id


def test_analysis_enqueue_failure_returns_typed_error(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session, queue=FailingAnalysisQueue())

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "analysis_enqueue_failed"
    assert _checkin_rows(db_session) == []


def test_update_enqueue_failure_keeps_existing_checkin_and_audit(
    db_session: Session,
) -> None:
    _seed_user_with_consent(db_session)
    initial_queue = FakeAnalysisQueue()
    client = _client(db_session, queue=initial_queue)

    create = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            mood_score=6,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )
    assert create.status_code == 200
    checkin_id = create.json()["data"]["checkin_id"]

    failing_client = _client(db_session, queue=FailingAnalysisQueue())
    update = failing_client.put(
        f"/v1/checkins/daily/{checkin_id}",
        json=_payload(
            energy_score=9,
            mood_score=8,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert update.status_code == 503
    assert update.json()["error"]["code"] == "analysis_enqueue_failed"
    rows = _checkin_rows(db_session)
    assert len(rows) == 1
    assert rows[0].energy_score == 5
    assert rows[0].mood_score == 6
    assert rows[0].analysis_job_id == initial_queue._job_id

    audits = _audit_events(db_session)
    assert [a.event_type for a in audits] == [AuditEventType.checkin_submitted]


def test_update_missing_checkin_returns_typed_error(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    response = client.put(
        f"/v1/checkins/daily/{uuid4()}",
        json=_payload(
            energy_score=5,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "checkin_not_found"


def test_delete_missing_checkin_returns_typed_error(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    response = client.delete(f"/v1/checkins/daily/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "checkin_not_found"


def test_allow_external_llm_requires_raw_note_consent(db_session: Session) -> None:
    _seed_user_with_consent(
        db_session,
        external_llm_enabled=True,
        raw_note_processing_enabled=False,
    )
    client = _client(db_session)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note="Raw note",
            sensitive_note_policy="allow_external_llm",
        ),
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "raw_note_disabled"
    assert _checkin_rows(db_session) == []


def test_allow_external_llm_does_not_persist_raw_note(
    db_session: Session,
) -> None:
    _seed_user_with_consent(
        db_session,
        external_llm_enabled=True,
        raw_note_processing_enabled=True,
    )
    client = _client(db_session)

    raw_note = "This raw note may be sent externally by a later explicit-consent flow."
    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note=raw_note,
            sensitive_note_policy="allow_external_llm",
        ),
    )

    assert response.status_code == 200
    assert response.json()["data"]["redaction_status"] == "none"
    rows = _checkin_rows(db_session)
    assert len(rows) == 1
    assert rows[0].free_text_note_reference is not None
    assert raw_note not in rows[0].free_text_note_reference
    assert rows[0].free_text_note_summary is None


def test_summarize_requires_external_llm_consent(db_session: Session) -> None:
    _seed_user_with_consent(
        db_session,
        external_llm_enabled=False,
        raw_note_processing_enabled=False,
    )
    client = _client(db_session)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note="Raw note",
            sensitive_note_policy="summarize_before_external_llm",
        ),
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "external_llm_disabled"
    assert _checkin_rows(db_session) == []


def test_missing_active_consent_returns_typed_error(db_session: Session) -> None:
    user = User(privacy_mode="local_only", active_consent_version="v1")
    db_session.add(user)
    db_session.flush()
    client = _client(db_session)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_missing"


def test_redaction_service_is_invoked_with_note_and_policy(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    redaction = FakeRedactionService()
    client = _client(db_session, redaction=redaction)

    response = client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note="My note",
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    assert response.status_code == 200
    assert len(redaction.calls) == 1
    assert redaction.calls[0] == ("My note", SensitiveNotePolicy.exclude_from_external_llm)


def test_audit_events_are_redacted(db_session: Session) -> None:
    _seed_user_with_consent(db_session)
    client = _client(db_session)

    client.post(
        "/v1/checkins/daily",
        json=_payload(
            energy_score=5,
            free_text_note="Should not appear in audit",
            sensitive_note_policy="exclude_from_external_llm",
        ),
    )

    audits = _audit_events(db_session)
    assert audits
    for audit in audits:
        assert audit.redaction_status in (
            ModelRedactionStatus.redacted,
            ModelRedactionStatus.partial,
            ModelRedactionStatus.none,
        )
        assert "Should not appear in audit" not in str(audit.event_metadata)
