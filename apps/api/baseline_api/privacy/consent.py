"""Consent lifecycle service."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import Session, col, select

from baseline_api.db.models.enums import AuditEventType, PrivacyMode
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.privacy.audit import emit_privacy_audit
from baseline_api.privacy.errors import PrivacyError
from baseline_api.privacy.user import get_single_user, list_single_user_candidates
from baseline_api.schemas.api import (
    ConsentHistoryResponse,
    ConsentRecordRequest,
    ConsentRecordResponse,
    ConsentRevocationRequest,
    DisableExternalLLMRequest,
)
from baseline_api.schemas.enums import HealthConsentCategory


class ConsentService:
    """Record and revoke versioned consent for the current MVP user."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_consent(self, request: ConsentRecordRequest) -> ConsentRecordResponse:
        now = datetime.now(UTC)
        _validate_consent_state(
            cloud_processing_enabled=request.cloud_processing_enabled,
            external_llm_enabled=request.external_llm_enabled,
            raw_note_processing_enabled=request.raw_note_processing_enabled,
        )
        privacy_mode = _privacy_mode_from_request(request)
        user = self._get_or_create_first_user(request)
        self._revoke_active_records(user, now)
        record = ConsentRecord(
            user_id=user.id,
            consent_version=request.consent_version,
            health_categories_enabled=_category_values(request.health_categories_enabled),
            cloud_processing_enabled=request.cloud_processing_enabled,
            external_llm_enabled=request.external_llm_enabled,
            raw_note_processing_enabled=request.raw_note_processing_enabled,
            timestamp=now,
        )
        self._session.add(record)
        user.active_consent_version = record.consent_version
        user.privacy_mode = privacy_mode
        self._session.add(user)
        emit_privacy_audit(
            self._session,
            event_type=AuditEventType.consent_granted,
            user_id=user.id,
            metadata={
                "consent_version": record.consent_version,
                "category_count": len(record.health_categories_enabled),
                "cloud_processing_enabled": record.cloud_processing_enabled,
                "external_llm_enabled": record.external_llm_enabled,
                "raw_note_processing_enabled": record.raw_note_processing_enabled,
            },
        )
        self._session.commit()
        return _consent_response(record)

    def _get_or_create_first_user(self, request: ConsentRecordRequest) -> User:
        users = list_single_user_candidates(self._session)
        if len(users) > 1:
            raise PrivacyError(
                code="ambiguous_user",
                message="Consent recording requires an authenticated user context.",
                status_code=409,
            )
        if users:
            return users[0]

        user = User(
            privacy_mode=_privacy_mode_from_request(request),
            active_consent_version=request.consent_version,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def disable_external_llm(self, request: DisableExternalLLMRequest) -> ConsentRecordResponse:
        user = get_single_user(self._session)
        active = self._active_consent(user.id)
        now = datetime.now(UTC)
        active.revoked_at = now
        self._session.add(active)
        record = ConsentRecord(
            user_id=user.id,
            consent_version=request.consent_version
            or f"{active.consent_version}-external-llm-disabled",
            health_categories_enabled=list(active.health_categories_enabled),
            cloud_processing_enabled=active.cloud_processing_enabled,
            external_llm_enabled=False,
            raw_note_processing_enabled=False,
            timestamp=now,
        )
        self._session.add(record)
        user.active_consent_version = record.consent_version
        user.privacy_mode = _privacy_mode(record)
        self._session.add(user)
        emit_privacy_audit(
            self._session,
            event_type=AuditEventType.consent_revoked,
            user_id=user.id,
            metadata={
                "previous_consent_version": active.consent_version,
                "consent_version": record.consent_version,
                "external_llm_enabled": False,
                "raw_note_processing_enabled": False,
            },
        )
        self._session.commit()
        return _consent_response(record)

    def revoke(self, request: ConsentRevocationRequest) -> ConsentRecordResponse:
        user = get_single_user(self._session)
        active = self._active_consent(user.id)
        now = datetime.now(UTC)

        categories = list(active.health_categories_enabled)
        if request.revoke_health_categories is not None:
            revoked_categories = set(_category_values(request.revoke_health_categories))
            categories = [category for category in categories if category not in revoked_categories]

        cloud_processing_enabled = (
            active.cloud_processing_enabled and not request.revoke_cloud_processing
        )
        external_llm_enabled = active.external_llm_enabled and not request.revoke_external_llm
        raw_note_processing_enabled = (
            active.raw_note_processing_enabled and not request.revoke_raw_note_processing
        )
        if not cloud_processing_enabled:
            external_llm_enabled = False
            raw_note_processing_enabled = False
        elif not external_llm_enabled:
            raw_note_processing_enabled = False
        _validate_consent_state(
            cloud_processing_enabled=cloud_processing_enabled,
            external_llm_enabled=external_llm_enabled,
            raw_note_processing_enabled=raw_note_processing_enabled,
        )

        active.revoked_at = now
        self._session.add(active)
        record = ConsentRecord(
            user_id=user.id,
            consent_version=request.consent_version or f"{active.consent_version}-revoked",
            health_categories_enabled=categories,
            cloud_processing_enabled=cloud_processing_enabled,
            external_llm_enabled=external_llm_enabled,
            raw_note_processing_enabled=raw_note_processing_enabled,
            timestamp=now,
        )
        self._session.add(record)
        user.active_consent_version = record.consent_version
        user.privacy_mode = _privacy_mode(record)
        self._session.add(user)
        emit_privacy_audit(
            self._session,
            event_type=AuditEventType.consent_revoked,
            user_id=user.id,
            metadata={
                "previous_consent_version": active.consent_version,
                "consent_version": record.consent_version,
                "category_count": len(record.health_categories_enabled),
                "cloud_processing_enabled": record.cloud_processing_enabled,
                "external_llm_enabled": record.external_llm_enabled,
                "raw_note_processing_enabled": record.raw_note_processing_enabled,
            },
        )
        self._session.commit()
        return _consent_response(record)

    def history(self) -> ConsentHistoryResponse:
        user = get_single_user(self._session)
        records = list(
            self._session.exec(
                select(ConsentRecord)
                .where(ConsentRecord.user_id == user.id)
                .order_by(col(ConsentRecord.timestamp).desc())
            ).all()
        )
        return ConsentHistoryResponse(
            active_consent_version=user.active_consent_version,
            records=[_consent_response(record) for record in records],
        )

    def _active_consent(self, user_id: UUID) -> ConsentRecord:
        record = self._session.exec(
            select(ConsentRecord)
            .where(
                ConsentRecord.user_id == user_id,
                col(ConsentRecord.revoked_at).is_(None),
            )
            .order_by(col(ConsentRecord.timestamp).desc())
        ).first()
        if record is None:
            raise PrivacyError(
                code="consent_missing",
                message="Active consent record not found.",
                status_code=403,
            )
        return record

    def _revoke_active_records(self, user: User, revoked_at: datetime) -> None:
        records = self._session.exec(
            select(ConsentRecord).where(
                ConsentRecord.user_id == user.id,
                col(ConsentRecord.revoked_at).is_(None),
            )
        ).all()
        for record in records:
            record.revoked_at = revoked_at
            self._session.add(record)


def _privacy_mode(record: ConsentRecord) -> PrivacyMode:
    if not record.cloud_processing_enabled:
        return PrivacyMode.local_only
    if record.external_llm_enabled:
        return PrivacyMode.cloud_assisted
    return PrivacyMode.hybrid


def _privacy_mode_from_request(request: ConsentRecordRequest) -> PrivacyMode:
    derived = _derived_privacy_mode_from_flags(request)
    if request.privacy_mode is not None:
        requested = PrivacyMode(request.privacy_mode.value)
        if requested != derived:
            raise PrivacyError(
                code="consent_inconsistent",
                message=(
                    "privacy_mode must match cloud_processing_enabled and "
                    "external_llm_enabled."
                ),
                status_code=400,
            )
        return requested
    return derived


def _derived_privacy_mode_from_flags(request: ConsentRecordRequest) -> PrivacyMode:
    if not request.cloud_processing_enabled:
        return PrivacyMode.local_only
    if request.external_llm_enabled:
        return PrivacyMode.cloud_assisted
    return PrivacyMode.hybrid


def _validate_consent_state(
    *,
    cloud_processing_enabled: bool,
    external_llm_enabled: bool,
    raw_note_processing_enabled: bool,
) -> None:
    if external_llm_enabled and not cloud_processing_enabled:
        raise PrivacyError(
            code="consent_inconsistent",
            message="External LLM consent requires cloud processing consent.",
            status_code=400,
        )
    if raw_note_processing_enabled and not (cloud_processing_enabled and external_llm_enabled):
        raise PrivacyError(
            code="consent_inconsistent",
            message="Raw note processing consent requires cloud and external LLM consent.",
            status_code=400,
        )


def _category_values(categories: Iterable[HealthConsentCategory]) -> list[str]:
    return [category.value for category in categories]


def _response_categories(categories: list[str]) -> list[HealthConsentCategory]:
    return [HealthConsentCategory(category) for category in categories]


def _consent_response(record: ConsentRecord) -> ConsentRecordResponse:
    return ConsentRecordResponse(
        id=record.id,
        user_id=record.user_id,
        consent_version=record.consent_version,
        health_categories_enabled=_response_categories(record.health_categories_enabled),
        cloud_processing_enabled=record.cloud_processing_enabled,
        external_llm_enabled=record.external_llm_enabled,
        raw_note_processing_enabled=record.raw_note_processing_enabled,
        timestamp=record.timestamp,
        revoked_at=record.revoked_at,
    )
