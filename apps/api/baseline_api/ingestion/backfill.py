"""Chunked historical Apple Health backfill orchestration."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from baseline_api.db.models.enums import MetricType
from baseline_api.db.models.ingestion import BackfillJob, HealthImportBatch, RawHealthSample
from baseline_api.db.repositories.ingestion import (
    BackfillJobRepository,
    HealthImportBatchRepository,
    RawHealthSampleRepository,
)
from baseline_api.ingestion.data_quality import DataQualityService
from baseline_api.ingestion.normalization import NormalizationService
from baseline_api.observability import metrics

SOURCE_PLATFORM = "apple_health"


@dataclass(frozen=True, slots=True)
class HistoricalSample:
    """A raw historical sample supplied by a HealthKit backfill source."""

    source_sample_id: str
    sample_type: MetricType
    start_time: dt.datetime
    end_time: dt.datetime | None
    value: float
    unit: str
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    job_id: UUID
    status: str
    processed_days: int
    next_start_date: dt.date
    accepted_count: int
    duplicate_count: int
    rejected_count: int


@dataclass(frozen=True, slots=True)
class _SampleDecision:
    sample: HistoricalSample
    content_hash: str
    rejected_reason: str | None = None


class BackfillService:
    """Import large historical ranges in resumable chunks."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._jobs = BackfillJobRepository(session)
        self._batches = HealthImportBatchRepository(session)
        self._samples = RawHealthSampleRepository(session)
        self._normalization = NormalizationService(session)
        self._quality = DataQualityService(session)

    def run(
        self,
        *,
        user_id: UUID,
        samples: list[HistoricalSample],
        start_date: dt.date,
        end_date: dt.date,
        chunk_days: int = 14,
        source_device: str = "unknown",
        timezone: str = "UTC",
        max_chunks: int | None = None,
    ) -> BackfillResult:
        """Run or resume a historical backfill over an inclusive date range."""

        if chunk_days < 1:
            raise ValueError("chunk_days must be at least 1")
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")

        started_at = perf_counter()
        end_exclusive = end_date + dt.timedelta(days=1)
        job = self._get_or_create_job(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            chunk_days=chunk_days,
            source_device=source_device,
            timezone=timezone,
        )

        try:
            if job.status == "completed":
                return _result(job)

            chunks_processed = 0
            while job.next_start_date < end_exclusive:
                if max_chunks is not None and chunks_processed >= max_chunks:
                    break

                chunk_start = job.next_start_date
                chunk_end = min(chunk_start + dt.timedelta(days=job.chunk_days), end_exclusive)
                accepted, duplicate, rejected = self._process_chunk(
                    job=job,
                    samples=_samples_for_range(samples, start_date=chunk_start, end_date=chunk_end),
                    chunk_start=chunk_start,
                    chunk_end=chunk_end,
                )
                job.accepted_count += accepted
                job.duplicate_count += duplicate
                job.rejected_count += rejected
                job.next_start_date = chunk_end
                job.processed_days = (job.next_start_date - job.start_date).days
                job.last_error = None
                chunks_processed += 1

                if job.next_start_date >= end_exclusive:
                    job.status = "completed"
                    job.completed_at = dt.datetime.now(dt.UTC)

                self._session.add(job)
                self._session.flush()

            return _result(job)
        finally:
            metrics.observe_backfill_duration(perf_counter() - started_at, source=SOURCE_PLATFORM)

    def _get_or_create_job(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
        chunk_days: int,
        source_device: str,
        timezone: str,
    ) -> BackfillJob:
        existing = self._jobs.get_by_range(
            user_id=user_id,
            source_platform=SOURCE_PLATFORM,
            start_date=start_date,
            end_date=end_date,
        )
        if existing is not None:
            return existing

        return self._jobs.create(
            BackfillJob(
                user_id=user_id,
                source_platform=SOURCE_PLATFORM,
                source_device=source_device,
                timezone=timezone,
                start_date=start_date,
                end_date=end_date,
                chunk_days=chunk_days,
                next_start_date=start_date,
                status="running",
            )
        )

    def _process_chunk(
        self,
        *,
        job: BackfillJob,
        samples: list[HistoricalSample],
        chunk_start: dt.date,
        chunk_end: dt.date,
    ) -> tuple[int, int, int]:
        client_sync_id = f"backfill:{job.id}:{chunk_start.isoformat()}:{chunk_end.isoformat()}"
        existing_batch = self._batches.get_by_client_sync_id(job.user_id, client_sync_id)
        if existing_batch is not None:
            self._normalization.normalize_batch(
                import_batch_id=existing_batch.id,
                user_id=job.user_id,
            )
            self._quality.compute_range(
                user_id=job.user_id,
                start_date=chunk_start,
                end_date=chunk_end,
            )
            return (
                existing_batch.accepted_count,
                existing_batch.duplicate_count,
                existing_batch.rejected_count,
            )

        imported_at = dt.datetime.now(dt.UTC)
        decisions = [_decide_sample(sample) for sample in samples]
        rejected_count = sum(1 for decision in decisions if decision.rejected_reason is not None)
        duplicate_count = 0
        accepted_samples: list[RawHealthSample] = []
        seen_source_hashes: set[tuple[str, str]] = set()

        batch = HealthImportBatch(
            user_id=job.user_id,
            client_sync_id=client_sync_id,
            request_hash=_request_hash(samples),
            source_platform=SOURCE_PLATFORM,
            source_device=job.source_device,
            timezone=job.timezone,
            last_anchor=None,
            next_anchor=f"{client_sync_id}:complete",
            accepted_count=0,
            duplicate_count=0,
            rejected_count=rejected_count,
            warnings=_warnings(rejected_count),
            data_quality_summary=_batch_quality_summary(rejected_count),
            imported_at=imported_at,
        )
        self._batches.create(batch)

        for decision in decisions:
            if decision.rejected_reason is not None:
                continue
            source_hash = (decision.sample.source_sample_id, decision.content_hash)
            if source_hash in seen_source_hashes:
                duplicate_count += 1
                continue
            duplicate = self._samples.get_by_source_hash(
                user_id=job.user_id,
                source_platform=SOURCE_PLATFORM,
                source_sample_id=decision.sample.source_sample_id,
                content_hash=decision.content_hash,
            )
            if duplicate is not None:
                duplicate_count += 1
                continue
            seen_source_hashes.add(source_hash)
            accepted_samples.append(
                _raw_sample(decision, job=job, batch=batch, imported_at=imported_at)
            )

        for sample in accepted_samples:
            self._samples.create(sample)

        batch.accepted_count = len(accepted_samples)
        batch.duplicate_count = duplicate_count
        self._session.add(batch)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise

        self._normalization.normalize_batch(import_batch_id=batch.id, user_id=job.user_id)
        self._quality.compute_range(
            user_id=job.user_id,
            start_date=chunk_start,
            end_date=chunk_end,
        )
        return len(accepted_samples), duplicate_count, rejected_count


def _samples_for_range(
    samples: list[HistoricalSample],
    *,
    start_date: dt.date,
    end_date: dt.date,
) -> list[HistoricalSample]:
    return [sample for sample in samples if start_date <= sample.start_time.date() < end_date]


def _decide_sample(sample: HistoricalSample) -> _SampleDecision:
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


def _raw_sample(
    decision: _SampleDecision,
    *,
    job: BackfillJob,
    batch: HealthImportBatch,
    imported_at: dt.datetime,
) -> RawHealthSample:
    sample = decision.sample
    return RawHealthSample(
        user_id=job.user_id,
        source_platform=SOURCE_PLATFORM,
        source_device=job.source_device,
        source_sample_id=sample.source_sample_id,
        content_hash=decision.content_hash,
        sample_type=sample.sample_type,
        start_time=sample.start_time,
        end_time=sample.end_time,
        raw_value=sample.value,
        raw_unit=sample.unit,
        source_metadata=sample.source_metadata,
        imported_at=imported_at,
        import_batch_id=batch.id,
    )


def _request_hash(samples: list[HistoricalSample]) -> str:
    payload = [_sample_payload(sample) for sample in samples]
    return _sha256_json({"samples": payload})


def _sample_content_hash(sample: HistoricalSample) -> str:
    return _sha256_json(_sample_payload(sample, include_source_id=False))


def _sample_payload(
    sample: HistoricalSample,
    *,
    include_source_id: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sample_type": sample.sample_type.value,
        "start_time": sample.start_time.isoformat(),
        "end_time": sample.end_time.isoformat() if sample.end_time is not None else None,
        "value": sample.value,
        "unit": sample.unit,
        "source_metadata": sample.source_metadata,
    }
    if include_source_id:
        payload["source_sample_id"] = sample.source_sample_id
    return payload


def _sha256_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _warnings(rejected_count: int) -> list[str]:
    if rejected_count == 0:
        return []
    return [f"{rejected_count} malformed backfill sample(s) were rejected."]


def _batch_quality_summary(rejected_count: int) -> dict[str, object]:
    if rejected_count == 0:
        return {"status": "ok", "notes": []}
    return {
        "status": "degraded",
        "notes": [
            {
                "note": f"{rejected_count} malformed backfill sample(s) were rejected.",
                "severity": "warning",
            }
        ],
    }


def _result(job: BackfillJob) -> BackfillResult:
    return BackfillResult(
        job_id=job.id,
        status=job.status,
        processed_days=job.processed_days,
        next_start_date=job.next_start_date,
        accepted_count=job.accepted_count,
        duplicate_count=job.duplicate_count,
        rejected_count=job.rejected_count,
    )
