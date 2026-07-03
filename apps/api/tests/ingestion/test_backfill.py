"""Tests for P1-03 historical backfill orchestration."""

from __future__ import annotations

import datetime as dt

from sqlmodel import Session, col, select

from baseline_api.db.models.enums import MetricType, PrivacyMode
from baseline_api.db.models.ingestion import (
    BackfillJob,
    DailyDataQuality,
    HealthImportBatch,
    NormalizedHealthMetric,
    RawHealthSample,
)
from baseline_api.db.models.sessions import SleepSession
from baseline_api.db.models.user import ConsentRecord, User
from baseline_api.ingestion.backfill import BackfillService, HistoricalSample


def _seed_user(session: Session) -> User:
    user = User(privacy_mode=PrivacyMode.local_only, active_consent_version="v1")
    session.add(user)
    session.flush()
    session.add(
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=["all"],
            timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        )
    )
    session.flush()
    return user


def _multi_month_fixture() -> list[HistoricalSample]:
    samples: list[HistoricalSample] = []
    for offset in range(92):
        day = dt.date(2026, 1, 1) + dt.timedelta(days=offset)
        samples.append(
            HistoricalSample(
                source_sample_id=f"hrv-{day.isoformat()}",
                sample_type=MetricType.heart_rate_variability,
                start_time=dt.datetime.combine(day, dt.time(7, 0), tzinfo=dt.UTC),
                end_time=dt.datetime.combine(day, dt.time(7, 5), tzinfo=dt.UTC),
                value=50.0 + (offset % 5),
                unit="ms",
                source_metadata={"synthetic": True},
            )
        )
        samples.append(
            HistoricalSample(
                source_sample_id=f"sleep-{day.isoformat()}",
                sample_type=MetricType.sleep_duration,
                start_time=dt.datetime.combine(day, dt.time(22, 0), tzinfo=dt.UTC),
                end_time=dt.datetime.combine(
                    day + dt.timedelta(days=1),
                    dt.time(5, 30),
                    tzinfo=dt.UTC,
                ),
                value=7.5,
                unit="h",
                source_metadata={"synthetic": True},
            )
        )
    return samples


def test_backfill_multi_month_fixture_resumes_and_is_idempotent(db_session: Session) -> None:
    user = _seed_user(db_session)
    service = BackfillService(db_session)
    samples = _multi_month_fixture()

    interrupted = service.run(
        user_id=user.id,
        samples=samples,
        start_date=dt.date(2026, 1, 1),
        end_date=dt.date(2026, 4, 2),
        chunk_days=14,
        max_chunks=1,
    )

    assert interrupted.status == "running"
    assert interrupted.processed_days == 14
    assert interrupted.next_start_date == dt.date(2026, 1, 15)
    assert db_session.exec(select(BackfillJob)).one().id == interrupted.job_id

    resumed = service.run(
        user_id=user.id,
        samples=samples,
        start_date=dt.date(2026, 1, 1),
        end_date=dt.date(2026, 4, 2),
        chunk_days=14,
    )

    assert resumed.job_id == interrupted.job_id
    assert resumed.status == "completed"
    assert resumed.processed_days == 92
    assert resumed.accepted_count == len(samples)
    assert resumed.duplicate_count == 0
    assert db_session.exec(select(RawHealthSample)).all()
    assert len(db_session.exec(select(RawHealthSample)).all()) == len(samples)
    assert len(db_session.exec(select(NormalizedHealthMetric)).all()) == 92
    assert len(db_session.exec(select(SleepSession)).all()) == 92
    assert len(db_session.exec(select(DailyDataQuality)).all()) == 92

    rerun = service.run(
        user_id=user.id,
        samples=samples,
        start_date=dt.date(2026, 1, 1),
        end_date=dt.date(2026, 4, 2),
        chunk_days=14,
    )

    assert rerun.job_id == resumed.job_id
    assert rerun.status == "completed"
    assert rerun.accepted_count == len(samples)
    assert rerun.duplicate_count == 0
    assert len(db_session.exec(select(RawHealthSample)).all()) == len(samples)
    assert len(db_session.exec(select(NormalizedHealthMetric)).all()) == 92
    assert len(db_session.exec(select(SleepSession)).all()) == 92
    assert len(db_session.exec(select(DailyDataQuality)).all()) == 92

    batches = db_session.exec(
        select(HealthImportBatch).where(
            col(HealthImportBatch.client_sync_id).startswith("backfill:")
        )
    ).all()
    assert len(batches) == 7


def test_backfill_resume_recovers_counts_from_flushed_chunk_batch(
    db_session: Session,
) -> None:
    user = _seed_user(db_session)
    service = BackfillService(db_session)
    samples = _multi_month_fixture()
    start_date = dt.date(2026, 1, 1)
    end_date = dt.date(2026, 1, 28)

    interrupted = service.run(
        user_id=user.id,
        samples=samples,
        start_date=start_date,
        end_date=end_date,
        chunk_days=14,
        max_chunks=1,
    )

    job = db_session.get(BackfillJob, interrupted.job_id)
    assert job is not None
    job.next_start_date = start_date
    job.processed_days = 0
    job.accepted_count = 0
    job.duplicate_count = 0
    job.rejected_count = 0
    db_session.add(job)
    db_session.flush()

    resumed = service.run(
        user_id=user.id,
        samples=samples,
        start_date=start_date,
        end_date=end_date,
        chunk_days=14,
    )

    expected_samples = [
        sample for sample in samples if start_date <= sample.start_time.date() <= end_date
    ]
    assert resumed.status == "completed"
    assert resumed.accepted_count == len(expected_samples)
    assert resumed.duplicate_count == 0
    assert resumed.rejected_count == 0
    assert len(db_session.exec(select(RawHealthSample)).all()) == len(expected_samples)
