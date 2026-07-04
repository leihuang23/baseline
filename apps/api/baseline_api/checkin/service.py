"""Daily check-in service with structured/free-text separation and audit logging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, select

from baseline_api.checkin.queue import AnalysisJobQueue
from baseline_api.checkin.redaction import NoteRedactionService
from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.enums import (
    AuditEventType,
)
from baseline_api.db.models.enums import (
    RedactionStatus as ModelRedactionStatus,
)
from baseline_api.db.models.enums import (
    SensitiveNotePolicy as ModelSensitiveNotePolicy,
)
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.db.repositories.audit import AuditEventRepository
from baseline_api.db.repositories.checkin import DailyCheckInRepository
from baseline_api.observability.logging import log_event
from baseline_api.schemas.api import (
    DailyCheckInDetailResponse,
    DailyCheckInFlags,
    DailyCheckInRequest,
    DailyCheckInResponse,
)
from baseline_api.schemas.enums import RedactionStatus, SensitiveNotePolicy


@dataclass(frozen=True)
class CheckinError(Exception):
    """Domain error raised by the check-in service."""

    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None


class CheckinService:
    """Create, update, and delete daily check-ins while enforcing privacy policies."""

    def __init__(
        self,
        session: Session,
        redaction: NoteRedactionService,
        queue: AnalysisJobQueue,
    ) -> None:
        self._session = session
        self._checkins = DailyCheckInRepository(session)
        self._audits = AuditEventRepository(session)
        self._redaction = redaction
        self._queue = queue

    async def create_checkin(self, request: DailyCheckInRequest) -> DailyCheckInResponse:
        user = self._get_single_user()
        consent = self._active_consent(user)
        self._assert_policy_consent(consent, request.sensitive_note_policy)
        redacted = await self._redaction.redact(
            request.free_text_note,
            self._model_policy(request.sensitive_note_policy),
        )

        checkin = self._to_model(request, redacted, user.id)
        self._checkins.create(checkin)

        try:
            checkin.analysis_job_id = await self._enqueue_analysis(checkin)
        except CheckinError:
            self._session.delete(checkin)
            self._session.commit()
            raise

        self._emit_audit(
            AuditEventType.checkin_submitted,
            user.id,
            checkin,
            request,
        )
        self._session.add(checkin)
        self._session.commit()
        log_event(
            "checkin.submitted",
            status="success",
            metadata=self._log_metadata(checkin, request),
        )
        return self._to_response(checkin, request)

    async def update_checkin(
        self,
        checkin_id: UUID,
        request: DailyCheckInRequest,
    ) -> DailyCheckInResponse:
        user = self._get_single_user()
        checkin = self._session.get(DailyCheckIn, checkin_id)
        if checkin is None or checkin.user_id != user.id:
            raise CheckinError(
                code="checkin_not_found",
                message="Check-in not found.",
                status_code=404,
            )

        consent = self._active_consent(user)
        self._assert_policy_consent(consent, request.sensitive_note_policy)
        preserve_existing_note = self._should_preserve_existing_note(checkin, request)
        if preserve_existing_note:
            redacted = None
        else:
            redacted = await self._redaction.redact(
                request.free_text_note,
                self._model_policy(request.sensitive_note_policy),
            )

        original = self._snapshot_checkin(checkin)
        self._apply_update(
            checkin,
            request,
            redacted,
            preserve_existing_note=preserve_existing_note,
        )
        try:
            checkin.analysis_job_id = await self._enqueue_analysis(checkin)
        except CheckinError:
            self._restore_checkin(checkin, original)
            self._session.add(checkin)
            self._session.commit()
            raise
        self._session.add(checkin)
        self._emit_audit(
            AuditEventType.checkin_updated,
            user.id,
            checkin,
            request,
        )
        self._session.commit()
        log_event(
            "checkin.updated",
            status="success",
            metadata=self._log_metadata(checkin, request),
        )
        return self._to_response(checkin, request)

    async def delete_checkin(self, checkin_id: UUID) -> None:
        user = self._get_single_user()
        checkin = self._session.get(DailyCheckIn, checkin_id)
        if checkin is None or checkin.user_id != user.id:
            raise CheckinError(
                code="checkin_not_found",
                message="Check-in not found.",
                status_code=404,
            )

        checkin_date = checkin.date
        self._session.delete(checkin)
        self._emit_raw_audit(
            AuditEventType.checkin_deleted,
            user.id,
            checkin_id,
            checkin_date,
        )
        self._session.commit()
        log_event(
            "checkin.deleted",
            status="success",
            metadata={
                "checkin_id": str(checkin_id),
                "date": checkin_date.isoformat(),
            },
        )

    def get_checkin_for_date(self, checkin_date: date) -> DailyCheckInDetailResponse:
        user = self._get_single_user()
        checkin = self._session.exec(
            select(DailyCheckIn).where(
                DailyCheckIn.user_id == user.id,
                DailyCheckIn.date == checkin_date,
            )
        ).first()
        if checkin is None:
            raise CheckinError(
                code="checkin_not_found",
                message="Check-in not found.",
                status_code=404,
            )
        return DailyCheckInDetailResponse(
            checkin_id=checkin.id,
            request=self._request_from_model(checkin),
            has_free_text_note=(
                checkin.free_text_note_reference is not None
                or checkin.free_text_note_summary is not None
            ),
        )

    def _get_single_user(self) -> User:
        users = list(self._session.exec(select(User).order_by(col(User.created_at)).limit(2)).all())
        if not users:
            raise CheckinError(
                code="user_not_initialized",
                message="No Baseline user is available for check-in.",
                status_code=409,
            )
        if len(users) > 1:
            raise CheckinError(
                code="ambiguous_user",
                message="Check-in requires an authenticated user context.",
                status_code=409,
            )
        return users[0]

    def _active_consent(self, user: User) -> ConsentRecord:
        statement = (
            select(ConsentRecord)
            .where(
                ConsentRecord.user_id == user.id,
                col(ConsentRecord.revoked_at).is_(None),
            )
            .order_by(col(ConsentRecord.timestamp).desc())
        )
        consent = self._session.exec(statement).first()
        if consent is None:
            raise CheckinError(
                code="consent_missing",
                message="Active consent record not found.",
                status_code=403,
            )
        return consent

    def _assert_policy_consent(
        self,
        consent: ConsentRecord,
        policy: SensitiveNotePolicy,
    ) -> None:
        if not consent.cloud_processing_enabled:
            raise CheckinError(
                code="cloud_processing_disabled",
                message="Cloud processing is not enabled in consent.",
                status_code=403,
            )
        if (
            policy
            in (
                SensitiveNotePolicy.summarize_before_external_llm,
                SensitiveNotePolicy.allow_external_llm,
            )
            and not consent.external_llm_enabled
        ):
            raise CheckinError(
                code="external_llm_disabled",
                message="External LLM processing is not enabled in consent.",
                status_code=403,
            )
        if (
            policy == SensitiveNotePolicy.allow_external_llm
            and not consent.raw_note_processing_enabled
        ):
            raise CheckinError(
                code="raw_note_disabled",
                message="Raw note processing is not enabled in consent.",
                status_code=403,
            )

    def _model_policy(
        self,
        policy: SensitiveNotePolicy,
    ) -> ModelSensitiveNotePolicy:
        return ModelSensitiveNotePolicy(policy.value)

    def _to_model(
        self,
        request: DailyCheckInRequest,
        redacted: Any,
        user_id: UUID,
    ) -> DailyCheckIn:
        return DailyCheckIn(
            user_id=user_id,
            date=request.date,
            energy_score=request.energy_score,
            mood_score=request.mood_score,
            soreness_score=request.soreness_score,
            stress_score=request.stress_score,
            perceived_recovery_score=request.perceived_recovery_score,
            food_quality_score=request.food_quality_score,
            alcohol_flag=request.flags.alcohol,
            caffeine_notes=request.flags.caffeine_notes,
            illness_flag=request.flags.illness,
            injury_flag=request.flags.injury,
            travel_flag=request.flags.travel,
            sensitive_note_policy=self._model_policy(request.sensitive_note_policy),
            redaction_status=self._model_redaction_status(redacted.status),
            structured_notes=request.structured_notes,
            free_text_note_reference=redacted.reference,
            free_text_note_summary=redacted.summary,
        )

    def _apply_update(
        self,
        checkin: DailyCheckIn,
        request: DailyCheckInRequest,
        redacted: Any,
        *,
        preserve_existing_note: bool = False,
    ) -> None:
        checkin.date = request.date
        checkin.energy_score = self._update_value(request, "energy_score", checkin.energy_score)
        checkin.mood_score = self._update_value(request, "mood_score", checkin.mood_score)
        checkin.soreness_score = self._update_value(
            request,
            "soreness_score",
            checkin.soreness_score,
        )
        checkin.stress_score = self._update_value(request, "stress_score", checkin.stress_score)
        checkin.perceived_recovery_score = self._update_value(
            request,
            "perceived_recovery_score",
            checkin.perceived_recovery_score,
        )
        checkin.food_quality_score = self._update_value(
            request,
            "food_quality_score",
            checkin.food_quality_score,
        )
        if "flags" in request.model_fields_set:
            checkin.alcohol_flag = request.flags.alcohol
            checkin.caffeine_notes = request.flags.caffeine_notes
            checkin.illness_flag = request.flags.illness
            checkin.injury_flag = request.flags.injury
            checkin.travel_flag = request.flags.travel
        if "structured_notes" in request.model_fields_set:
            checkin.structured_notes = request.structured_notes
        if not preserve_existing_note:
            checkin.sensitive_note_policy = self._model_policy(request.sensitive_note_policy)
            checkin.redaction_status = self._model_redaction_status(redacted.status)
            checkin.free_text_note_reference = redacted.reference
            checkin.free_text_note_summary = redacted.summary

    def _should_preserve_existing_note(
        self,
        checkin: DailyCheckIn,
        request: DailyCheckInRequest,
    ) -> bool:
        return (
            "free_text_note" not in request.model_fields_set
            and self._model_policy(request.sensitive_note_policy) == checkin.sensitive_note_policy
            and (
                checkin.free_text_note_reference is not None
                or checkin.free_text_note_summary is not None
            )
        )

    def _update_value(
        self,
        request: DailyCheckInRequest,
        field: str,
        current_value: Any,
    ) -> Any:
        if field in request.model_fields_set:
            return getattr(request, field)
        return current_value

    def _snapshot_checkin(self, checkin: DailyCheckIn) -> dict[str, Any]:
        return {
            "date": checkin.date,
            "energy_score": checkin.energy_score,
            "mood_score": checkin.mood_score,
            "soreness_score": checkin.soreness_score,
            "stress_score": checkin.stress_score,
            "perceived_recovery_score": checkin.perceived_recovery_score,
            "food_quality_score": checkin.food_quality_score,
            "alcohol_flag": checkin.alcohol_flag,
            "caffeine_notes": checkin.caffeine_notes,
            "illness_flag": checkin.illness_flag,
            "injury_flag": checkin.injury_flag,
            "travel_flag": checkin.travel_flag,
            "sensitive_note_policy": checkin.sensitive_note_policy,
            "redaction_status": checkin.redaction_status,
            "structured_notes": dict(checkin.structured_notes),
            "free_text_note_reference": checkin.free_text_note_reference,
            "free_text_note_summary": checkin.free_text_note_summary,
            "analysis_job_id": checkin.analysis_job_id,
        }

    def _restore_checkin(
        self,
        checkin: DailyCheckIn,
        snapshot: dict[str, Any],
    ) -> None:
        for field, value in snapshot.items():
            setattr(checkin, field, value)

    async def _enqueue_analysis(self, checkin: DailyCheckIn) -> UUID:
        try:
            job_id = await self._queue.enqueue_daily_analysis(
                checkin_id=checkin.id,
                user_id=checkin.user_id,
                date=checkin.date,
            )
        except Exception as exc:
            log_event(
                "checkin.analysis_enqueue_failed",
                status="error",
                metadata={"checkin_id": str(checkin.id)},
                level="warning",
            )
            raise CheckinError(
                code="analysis_enqueue_failed",
                message="Failed to enqueue daily analysis job.",
                status_code=503,
            ) from exc
        if job_id is None:
            log_event(
                "checkin.analysis_enqueue_failed",
                status="error",
                metadata={"checkin_id": str(checkin.id)},
                level="warning",
            )
            raise CheckinError(
                code="analysis_enqueue_failed",
                message="Failed to enqueue daily analysis job.",
                status_code=503,
            )
        return job_id

    def _emit_audit(
        self,
        event_type: AuditEventType,
        user_id: UUID,
        checkin: DailyCheckIn,
        request: DailyCheckInRequest,
    ) -> None:
        status = self._response_redaction_status(checkin)
        event = AuditEvent(
            user_id=user_id,
            event_type=event_type,
            actor="user",
            timestamp=datetime.now(UTC),
            event_metadata={
                "checkin_id": str(checkin.id),
                "date": checkin.date.isoformat(),
                "accepted_fields": self._accepted_fields(request),
                "redaction_status": status.value,
                "analysis_job_id": str(checkin.analysis_job_id)
                if checkin.analysis_job_id is not None
                else None,
            },
            redaction_status=self._model_redaction_status(status),
        )
        self._audits.create(event)

    def _emit_raw_audit(
        self,
        event_type: AuditEventType,
        user_id: UUID,
        checkin_id: UUID,
        checkin_date: Any,
    ) -> None:
        event = AuditEvent(
            user_id=user_id,
            event_type=event_type,
            actor="user",
            timestamp=datetime.now(UTC),
            event_metadata={
                "checkin_id": str(checkin_id),
                "date": checkin_date.isoformat(),
            },
            redaction_status=ModelRedactionStatus.none,
        )
        self._audits.create(event)

    def _accepted_fields(self, request: DailyCheckInRequest) -> list[str]:
        fields: list[str] = []
        score_fields = [
            "energy_score",
            "mood_score",
            "soreness_score",
            "stress_score",
            "perceived_recovery_score",
            "food_quality_score",
        ]
        for field in score_fields:
            if getattr(request, field) is not None:
                fields.append(field)

        if request.flags.alcohol:
            fields.append("alcohol_flag")
        if request.flags.caffeine_notes:
            fields.append("caffeine_notes")
        if request.flags.illness:
            fields.append("illness_flag")
        if request.flags.injury:
            fields.append("injury_flag")
        if request.flags.travel:
            fields.append("travel_flag")

        if request.structured_notes:
            fields.append("structured_notes")
        if request.free_text_note:
            fields.append("free_text_note")

        return fields

    def _to_response(
        self,
        checkin: DailyCheckIn,
        request: DailyCheckInRequest,
    ) -> DailyCheckInResponse:
        return DailyCheckInResponse(
            checkin_id=checkin.id,
            accepted_fields=self._accepted_fields(request),
            redaction_status=self._response_redaction_status(checkin),
            analysis_job_id=checkin.analysis_job_id,
        )

    def _request_from_model(self, checkin: DailyCheckIn) -> DailyCheckInRequest:
        return DailyCheckInRequest(
            date=checkin.date,
            energy_score=checkin.energy_score,
            mood_score=checkin.mood_score,
            soreness_score=checkin.soreness_score,
            stress_score=checkin.stress_score,
            perceived_recovery_score=checkin.perceived_recovery_score,
            food_quality_score=checkin.food_quality_score,
            flags=DailyCheckInFlags(
                alcohol=checkin.alcohol_flag,
                caffeine_notes=checkin.caffeine_notes,
                illness=checkin.illness_flag,
                injury=checkin.injury_flag,
                travel=checkin.travel_flag,
            ),
            structured_notes=checkin.structured_notes,
            free_text_note=None,
            sensitive_note_policy=SensitiveNotePolicy(checkin.sensitive_note_policy.value),
        )

    def _response_redaction_status(self, checkin: DailyCheckIn) -> RedactionStatus:
        return RedactionStatus(checkin.redaction_status.value)

    def _model_redaction_status(
        self,
        status: RedactionStatus,
    ) -> ModelRedactionStatus:
        return ModelRedactionStatus(status.value)

    def _log_metadata(
        self,
        checkin: DailyCheckIn,
        request: DailyCheckInRequest,
    ) -> dict[str, Any]:
        return {
            "checkin_id": str(checkin.id),
            "date": checkin.date.isoformat(),
            "accepted_fields": self._accepted_fields(request),
            "redaction_status": self._response_redaction_status(checkin).value,
            "analysis_job_id": str(checkin.analysis_job_id)
            if checkin.analysis_job_id is not None
            else None,
            "free_text_note_reference": checkin.free_text_note_reference,
        }
