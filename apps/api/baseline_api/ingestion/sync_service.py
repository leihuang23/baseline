"""Health sync ingestion service."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from baseline_api.db.models.enums import MetricType
from baseline_api.db.models.ingestion import HealthImportBatch, RawHealthSample
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.db.repositories.ingestion import (
    HealthImportBatchRepository,
    RawHealthSampleRepository,
)
from baseline_api.observability import metrics
from baseline_api.observability.logging import log_event
from baseline_api.schemas.api import (
    DataQualitySummary,
    HealthSamplePayload,
    HealthSyncRequest,
    HealthSyncResponse,
)
from baseline_api.schemas.enums import HealthConsentCategory

SOURCE_PLATFORM = "apple_health"

_CATEGORY_BY_SAMPLE_TYPE = {
    "heart_rate_variability": "heart_rate",
    "resting_heart_rate": "heart_rate",
    "steps": "activity",
    "active_energy": "activity",
    "vo2_max": "activity",
    "workout": "activity",
    "sleep_duration": "sleep",
    "blood_oxygen": "vitals",
    "body_temperature": "vitals",
    "other": "other",
}


@dataclass(frozen=True)
class IngestionError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class _SampleDecision:
    sample: HealthSamplePayload
    content_hash: str
    rejected_reason: str | None = None


@dataclass(frozen=True)
class PendingNormalizationJob:
    import_batch_id: UUID
    user_id: UUID


@dataclass(frozen=True)
class HealthSyncResult:
    response: HealthSyncResponse
    pending_normalization: PendingNormalizationJob | None = None


class HealthSyncService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._batches = HealthImportBatchRepository(session)
        self._samples = RawHealthSampleRepository(session)

    def sync(
        self,
        request: HealthSyncRequest,
        *,
        retry_on_integrity_error: bool = True,
    ) -> HealthSyncResult:
        started_at = datetime.now(UTC)
        user = self._get_single_user()
        self._assert_consent(user, request)

        request_hash = _request_hash(request)
        existing_batch = self._batches.get_by_client_sync_id(user.id, request.client_sync_id)
        if existing_batch is not None:
            if existing_batch.request_hash != request_hash:
                raise IngestionError(
                    code="idempotency_key_conflict",
                    message="client_sync_id was already used with different sync content.",
                    status_code=409,
                )
            response = _replay_response(existing_batch)
            _record_completion(started_at=started_at, response=response)
            pending_normalization = (
                PendingNormalizationJob(import_batch_id=existing_batch.id, user_id=user.id)
                if existing_batch.accepted_count > 0 and existing_batch.normalization_job_id is None
                else None
            )
            return HealthSyncResult(
                response=response,
                pending_normalization=pending_normalization,
            )

        decisions = [_decide_sample(sample) for sample in request.samples]
        rejected_count = sum(1 for decision in decisions if decision.rejected_reason is not None)
        accepted_samples: list[RawHealthSample] = []
        duplicate_count = 0
        seen_source_hashes: set[tuple[str, str]] = set()
        imported_at = datetime.now(UTC)
        batch_id = uuid4()

        for decision in decisions:
            if decision.rejected_reason is not None:
                continue

            source_hash = (decision.sample.source_sample_id, decision.content_hash)
            if source_hash in seen_source_hashes:
                duplicate_count += 1
                continue

            duplicate = self._samples.get_by_source_hash(
                user_id=user.id,
                source_platform=SOURCE_PLATFORM,
                source_sample_id=decision.sample.source_sample_id,
                content_hash=decision.content_hash,
            )
            if duplicate is not None:
                duplicate_count += 1
                continue

            seen_source_hashes.add(source_hash)
            accepted_samples.append(
                RawHealthSample(
                    user_id=user.id,
                    source_platform=SOURCE_PLATFORM,
                    source_device=request.device_id,
                    source_sample_id=decision.sample.source_sample_id,
                    content_hash=decision.content_hash,
                    sample_type=MetricType(decision.sample.sample_type.value),
                    start_time=decision.sample.start_time,
                    end_time=decision.sample.end_time,
                    raw_value=decision.sample.value,
                    raw_unit=decision.sample.unit,
                    source_metadata=decision.sample.source_metadata,
                    imported_at=imported_at,
                    import_batch_id=batch_id,
                )
            )

        warnings = _warnings(rejected_count=rejected_count)
        data_quality = _data_quality_summary(
            accepted_count=len(accepted_samples),
            duplicate_count=duplicate_count,
            rejected_count=rejected_count,
        )
        batch = HealthImportBatch(
            id=batch_id,
            user_id=user.id,
            client_sync_id=request.client_sync_id,
            request_hash=request_hash,
            source_platform=SOURCE_PLATFORM,
            source_device=request.device_id,
            timezone=request.timezone,
            last_anchor=request.last_anchor,
            next_anchor="pending",
            accepted_count=len(accepted_samples),
            duplicate_count=duplicate_count,
            rejected_count=rejected_count,
            warnings=warnings,
            data_quality_summary=data_quality.model_dump(mode="json"),
            imported_at=imported_at,
        )
        batch.next_anchor = _next_anchor(batch, request)
        try:
            self._batches.create(batch)
            for sample in accepted_samples:
                sample.import_batch_id = batch.id
                self._samples.create(sample)
        except IntegrityError as error:
            self._session.rollback()
            if retry_on_integrity_error:
                return self.sync(request, retry_on_integrity_error=False)
            raise IngestionError(
                code="ingestion_conflict",
                message="Health sync conflicted with another in-flight ingestion.",
                status_code=409,
            ) from error

        response = _response(batch)
        _record_completion(started_at=started_at, response=response)
        pending_normalization = (
            PendingNormalizationJob(import_batch_id=batch.id, user_id=user.id)
            if accepted_samples
            else None
        )
        return HealthSyncResult(response=response, pending_normalization=pending_normalization)

    def _get_single_user(self) -> User:
        users = list(self._session.exec(select(User).order_by(col(User.created_at)).limit(2)).all())
        if not users:
            raise IngestionError(
                code="user_not_initialized",
                message="No Baseline user is available for health sync.",
                status_code=409,
            )
        if len(users) > 1:
            raise IngestionError(
                code="ambiguous_user",
                message="Health sync requires an authenticated user context.",
                status_code=409,
            )
        return users[0]

    def _assert_consent(self, user: User, request: HealthSyncRequest) -> None:
        if user.active_consent_version != request.consent_version:
            raise IngestionError(
                code="consent_invalid",
                message="Consent version is not active for health ingestion.",
                status_code=403,
            )

        statement = (
            select(ConsentRecord)
            .where(
                ConsentRecord.user_id == user.id,
                ConsentRecord.consent_version == request.consent_version,
                col(ConsentRecord.revoked_at).is_(None),
            )
            .order_by(col(ConsentRecord.timestamp).desc())
        )
        consent = self._session.exec(statement).first()
        if consent is None:
            raise IngestionError(
                code="consent_invalid",
                message="Consent is missing or revoked for health ingestion.",
                status_code=403,
            )
        if not consent.cloud_processing_enabled:
            raise IngestionError(
                code="cloud_processing_disabled",
                message="Consent does not allow server-side health ingestion.",
                status_code=403,
            )

        enabled = set(consent.health_categories_enabled)
        missing_categories = sorted(
            {
                _CATEGORY_BY_SAMPLE_TYPE[sample.sample_type.value]
                for sample in request.samples
                if not _category_enabled(enabled, sample.sample_type.value)
            }
        )
        if missing_categories:
            raise IngestionError(
                code="consent_category_disabled",
                message="Consent does not allow one or more requested health categories.",
                status_code=403,
                details={"categories": missing_categories},
            )


def _category_enabled(enabled: set[str], sample_type: str) -> bool:
    return (
        HealthConsentCategory.all.value in enabled
        or _CATEGORY_BY_SAMPLE_TYPE[sample_type] in enabled
    )


def _decide_sample(sample: HealthSamplePayload) -> _SampleDecision:
    content_hash = _sample_content_hash(sample)
    if sample.end_time is not None and sample.end_time < sample.start_time:
        return _SampleDecision(
            sample=sample,
            content_hash=content_hash,
            rejected_reason="time_order",
        )
    if not math.isfinite(sample.value) or sample.value < 0:
        return _SampleDecision(sample=sample, content_hash=content_hash, rejected_reason="value")
    return _SampleDecision(sample=sample, content_hash=content_hash)


def _request_hash(request: HealthSyncRequest) -> str:
    return _sha256_json(request.model_dump(mode="json"))


def _sample_content_hash(sample: HealthSamplePayload) -> str:
    payload = sample.model_dump(mode="json", exclude={"source_sample_id"})
    return _sha256_json(payload)


def _sha256_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _next_anchor(batch: HealthImportBatch, request: HealthSyncRequest) -> str:
    sample_times = [sample.end_time or sample.start_time for sample in request.samples]
    anchor_time = max(sample_times) if sample_times else batch.imported_at
    return f"health-sync:{batch.id}:{anchor_time.isoformat()}"


def _warnings(*, rejected_count: int) -> list[str]:
    if rejected_count == 0:
        return []
    return [f"{rejected_count} malformed sample(s) were rejected."]


def _data_quality_summary(
    *,
    accepted_count: int,
    duplicate_count: int,
    rejected_count: int,
) -> DataQualitySummary:
    notes: list[dict[str, str]] = []
    if rejected_count:
        notes.append(
            {
                "note": f"{rejected_count} malformed sample(s) were rejected.",
                "severity": "warning",
            }
        )
    if accepted_count == 0 and duplicate_count == 0:
        notes.append({"note": "No usable health samples were included.", "severity": "degraded"})
        status = "insufficient"
    elif rejected_count:
        status = "degraded"
    else:
        status = "ok"
    return DataQualitySummary.model_validate({"status": status, "notes": notes})


def _response(batch: HealthImportBatch) -> HealthSyncResponse:
    return HealthSyncResponse(
        sync_id=batch.id,
        accepted_count=batch.accepted_count,
        duplicate_count=batch.duplicate_count,
        rejected_count=batch.rejected_count,
        warnings=batch.warnings,
        next_anchor=batch.next_anchor,
        data_quality_summary=DataQualitySummary.model_validate(batch.data_quality_summary),
    )


def _replay_response(batch: HealthImportBatch) -> HealthSyncResponse:
    return HealthSyncResponse(
        sync_id=batch.id,
        accepted_count=0,
        duplicate_count=batch.accepted_count + batch.duplicate_count,
        rejected_count=batch.rejected_count,
        warnings=batch.warnings,
        next_anchor=batch.next_anchor,
        data_quality_summary=DataQualitySummary.model_validate(batch.data_quality_summary),
    )


def _record_completion(*, started_at: datetime, response: HealthSyncResponse) -> None:
    latency_seconds = (datetime.now(UTC) - started_at).total_seconds()
    _record_metrics(
        accepted_count=response.accepted_count,
        duplicate_count=response.duplicate_count,
        rejected_count=response.rejected_count,
        latency_seconds=latency_seconds,
    )
    log_event(
        "health.sync_completed",
        status="success",
        metadata={
            "accepted_count": response.accepted_count,
            "duplicate_count": response.duplicate_count,
            "rejected_count": response.rejected_count,
        },
    )


def _record_metrics(
    *,
    accepted_count: int,
    duplicate_count: int,
    rejected_count: int,
    latency_seconds: float,
) -> None:
    total_count = accepted_count + duplicate_count + rejected_count
    duplicate_rate = duplicate_count / total_count if total_count else 0.0
    metrics.increment_sync_success(source=SOURCE_PLATFORM)
    metrics.observe_sync_latency(latency_seconds, source=SOURCE_PLATFORM)
    metrics.set_duplicate_sample_rate(duplicate_rate, source=SOURCE_PLATFORM)
    for _ in range(rejected_count):
        metrics.increment_rejected_sample_count(reason="malformed")
