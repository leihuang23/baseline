"""Hard-delete privacy controls."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from baseline_api.db.models import (
    AuditEvent,
    BackfillJob,
    ConsentRecord,
    DailyAnalysisJob,
    DailyCheckIn,
    DailyDataQuality,
    DerivedDailyFeature,
    DerivedDailyFeatureSourceMetric,
    DerivedDailyFeatureSourceSample,
    Goal,
    HealthImportBatch,
    MemorySummary,
    ModelRun,
    NormalizedHealthMetric,
    NormalizedHealthMetricSourceSample,
    RawHealthSample,
    ReadinessAssessment,
    ReasoningTrace,
    Recommendation,
    SleepSession,
    SleepSessionSourceSample,
    User,
    WorkoutSession,
    WorkoutSessionSourceSample,
)
from baseline_api.db.models.enums import AuditEventType, RedactionStatus
from baseline_api.privacy.audit import emit_privacy_audit
from baseline_api.privacy.errors import PrivacyError
from baseline_api.privacy.export import LocalExportStore
from baseline_api.privacy.model_runs import model_run_ids_from_payload
from baseline_api.privacy.user import get_single_user
from baseline_api.schemas.api import DataDeleteResponse


class DataDeletionService:
    """Delete all user-owned records or granular sensitive entities."""

    def __init__(self, session: Session, export_store: LocalExportStore | None = None) -> None:
        self._session = session
        self._export_store = export_store

    def delete_all(self, *, user: User | None = None) -> DataDeleteResponse:
        resolved_user = user or get_single_user(self._session)
        try:
            return self._delete_all_for_user(resolved_user)
        except PrivacyError:
            raise
        except Exception as exc:
            self._record_deletion_failure(
                event_type=AuditEventType.data_deleted,
                user_id=resolved_user.id,
                target="all",
                exc=exc,
            )
            raise

    def _delete_all_for_user(self, user: Any) -> DataDeleteResponse:
        counts: dict[str, int] = {}

        raw_ids = self._ids(RawHealthSample, user.id)
        metric_ids = self._ids(NormalizedHealthMetric, user.id)
        feature_ids = self._ids(DerivedDailyFeature, user.id)
        workout_ids = self._ids(WorkoutSession, user.id)
        sleep_ids = self._ids(SleepSession, user.id)
        if self._export_store is not None:
            counts["exports"] = self._export_store.purge_user(user.id)

        counts["daily_analysis_jobs"] = self._delete_user_rows(DailyAnalysisJob, user.id)
        counts["recommendations"] = self._delete_user_rows(Recommendation, user.id)
        counts["readiness_assessments"] = self._delete_user_rows(ReadinessAssessment, user.id)
        counts["reasoning_traces"] = self._delete_user_rows(ReasoningTrace, user.id)
        counts["model_runs"] = self._delete_user_rows(ModelRun, user.id)
        counts["memory_summaries"] = self._delete_user_rows(MemorySummary, user.id)
        counts["daily_check_ins"] = self._delete_user_rows(DailyCheckIn, user.id)
        counts["goals"] = self._delete_user_rows(Goal, user.id)
        counts["daily_data_quality"] = self._delete_user_rows(DailyDataQuality, user.id)
        counts["backfill_jobs"] = self._delete_user_rows(BackfillJob, user.id)

        counts["derived_feature_source_metrics"] = self._delete_matching(
            select(DerivedDailyFeatureSourceMetric).where(
                col(DerivedDailyFeatureSourceMetric.derived_daily_feature_id).in_(feature_ids)
                | col(DerivedDailyFeatureSourceMetric.normalized_health_metric_id).in_(metric_ids)
            )
        )
        counts["derived_feature_source_samples"] = self._delete_matching(
            select(DerivedDailyFeatureSourceSample).where(
                col(DerivedDailyFeatureSourceSample.derived_daily_feature_id).in_(feature_ids)
                | col(DerivedDailyFeatureSourceSample.raw_health_sample_id).in_(raw_ids)
            )
        )
        counts["workout_source_samples"] = self._delete_matching(
            select(WorkoutSessionSourceSample).where(
                col(WorkoutSessionSourceSample.workout_session_id).in_(workout_ids)
                | col(WorkoutSessionSourceSample.raw_health_sample_id).in_(raw_ids)
            )
        )
        counts["sleep_source_samples"] = self._delete_matching(
            select(SleepSessionSourceSample).where(
                col(SleepSessionSourceSample.sleep_session_id).in_(sleep_ids)
                | col(SleepSessionSourceSample.raw_health_sample_id).in_(raw_ids)
            )
        )
        counts["normalized_source_samples"] = self._delete_matching(
            select(NormalizedHealthMetricSourceSample).where(
                col(NormalizedHealthMetricSourceSample.normalized_health_metric_id).in_(metric_ids)
                | col(NormalizedHealthMetricSourceSample.raw_health_sample_id).in_(raw_ids)
            )
        )

        counts["derived_daily_features"] = self._delete_user_rows(DerivedDailyFeature, user.id)
        counts["sleep_sessions"] = self._delete_user_rows(SleepSession, user.id)
        counts["workout_sessions"] = self._delete_user_rows(WorkoutSession, user.id)
        counts["normalized_health_metrics"] = self._delete_user_rows(
            NormalizedHealthMetric,
            user.id,
        )
        counts["raw_health_samples"] = self._delete_user_rows(RawHealthSample, user.id)
        counts["health_import_batches"] = self._delete_user_rows(HealthImportBatch, user.id)
        counts["consent_records"] = self._delete_user_rows(ConsentRecord, user.id)
        counts["audit_events"] = self._delete_user_rows(AuditEvent, user.id)
        counts["users"] = 1

        deleted_user_hash = hashlib.sha256(str(user.id).encode("utf-8")).hexdigest()
        self._session.delete(user)
        emit_privacy_audit(
            self._session,
            event_type=AuditEventType.data_deleted,
            user_id=None,
            metadata={
                "target": "all",
                "deleted": counts,
                "deleted_user_hash": deleted_user_hash,
            },
        )
        self._session.commit()
        return DataDeleteResponse(deleted=counts)

    def delete_note(
        self,
        checkin_id: UUID,
        *,
        user: User | None = None,
    ) -> DataDeleteResponse:
        resolved_user = user or get_single_user(self._session)
        checkin = self._checkin(resolved_user.id, checkin_id)
        try:
            had_note = int(
                checkin.free_text_note_reference is not None
                or checkin.free_text_note_summary is not None
            )
            checkin.free_text_note_reference = None
            checkin.free_text_note_summary = None
            checkin.redaction_status = RedactionStatus.none
            self._session.add(checkin)
            derived_counts = self._delete_note_derived_artifacts(
                user_id=resolved_user.id,
                checkin_id=checkin_id,
            )
            emit_privacy_audit(
                self._session,
                event_type=AuditEventType.data_deleted,
                user_id=resolved_user.id,
                metadata={"target": "checkin_note", "checkin_id": str(checkin_id)},
            )
            self._session.commit()
            return DataDeleteResponse(deleted={"checkin_notes": had_note, **derived_counts})
        except PrivacyError:
            raise
        except Exception as exc:
            self._record_deletion_failure(
                event_type=AuditEventType.data_deleted,
                user_id=resolved_user.id,
                target="checkin_note",
                target_id=checkin_id,
                exc=exc,
            )
            raise

    def delete_checkin(
        self,
        checkin_id: UUID,
        *,
        user: User | None = None,
    ) -> DataDeleteResponse:
        resolved_user = user or get_single_user(self._session)
        checkin = self._checkin(resolved_user.id, checkin_id)
        try:
            with self._session.begin_nested():
                derived_counts = self._delete_checkin_derived_artifacts(
                    user_id=resolved_user.id,
                    checkin_id=checkin.id,
                    checkin_date=checkin.date,
                )
                self._session.delete(checkin)
        except PrivacyError:
            raise
        except Exception as exc:
            self._record_deletion_failure(
                event_type=AuditEventType.data_deleted,
                user_id=resolved_user.id,
                target="checkin",
                target_id=checkin_id,
                exc=exc,
                rollback=False,
            )
            raise

        try:
            emit_privacy_audit(
                self._session,
                event_type=AuditEventType.data_deleted,
                user_id=resolved_user.id,
                metadata={"target": "checkin", "checkin_id": str(checkin_id)},
            )
            self._session.commit()
            return DataDeleteResponse(deleted={"daily_check_ins": 1, **derived_counts})
        except PrivacyError:
            raise
        except Exception as exc:
            self._record_deletion_failure(
                event_type=AuditEventType.data_deleted,
                user_id=resolved_user.id,
                target="checkin",
                target_id=checkin_id,
                exc=exc,
            )
            raise

    def delete_memory_summary(
        self,
        memory_summary_id: UUID,
        *,
        user: User | None = None,
    ) -> DataDeleteResponse:
        resolved_user = user or get_single_user(self._session)
        memory = self._session.get(MemorySummary, memory_summary_id)
        if memory is None or memory.user_id != resolved_user.id:
            raise PrivacyError(
                code="memory_summary_not_found",
                message="Memory summary not found.",
                status_code=404,
            )
        try:
            self._session.delete(memory)
            emit_privacy_audit(
                self._session,
                event_type=AuditEventType.memory_deleted,
                user_id=resolved_user.id,
                metadata={"target": "memory_summary", "memory_summary_id": str(memory_summary_id)},
            )
            self._session.commit()
            return DataDeleteResponse(deleted={"memory_summaries": 1})
        except PrivacyError:
            raise
        except Exception as exc:
            self._record_deletion_failure(
                event_type=AuditEventType.memory_deleted,
                user_id=resolved_user.id,
                target="memory_summary",
                target_id=memory_summary_id,
                exc=exc,
            )
            raise

    def _checkin(self, user_id: UUID, checkin_id: UUID) -> DailyCheckIn:
        checkin = self._session.get(DailyCheckIn, checkin_id)
        if checkin is None or checkin.user_id != user_id:
            raise PrivacyError(
                code="checkin_not_found",
                message="Check-in not found.",
                status_code=404,
            )
        return checkin

    def _ids(self, model: type[Any], user_id: UUID) -> list[UUID]:
        return [
            row.id
            for row in self._session.exec(select(model).where(model.user_id == user_id)).all()
        ]

    def _delete_checkin_derived_artifacts(
        self,
        *,
        user_id: UUID,
        checkin_id: UUID,
        checkin_date: dt.date,
    ) -> dict[str, int]:
        jobs = list(
            self._session.exec(
                select(DailyAnalysisJob).where(
                    DailyAnalysisJob.user_id == user_id,
                    DailyAnalysisJob.date == checkin_date,
                )
            ).all()
        )
        recommendations = list(
            self._session.exec(
                select(Recommendation).where(
                    Recommendation.user_id == user_id,
                    Recommendation.date == checkin_date,
                )
            ).all()
        )
        assessments = list(
            self._session.exec(
                select(ReadinessAssessment).where(
                    ReadinessAssessment.user_id == user_id,
                    ReadinessAssessment.date == checkin_date,
                )
            ).all()
        )
        trace_ids = _unique_ids(
            [
                *[job.reasoning_trace_id for job in jobs],
                *[recommendation.reasoning_trace_id for recommendation in recommendations],
                *[assessment.reasoning_trace_id for assessment in assessments],
            ]
        )
        traces = [
            trace
            for trace_id in trace_ids
            if (trace := self._session.get(ReasoningTrace, trace_id)) is not None
            and trace.user_id == user_id
        ]
        model_run_ids = _unique_ids(
            [
                *[recommendation.model_run_id for recommendation in recommendations],
                *[
                    model_run_id
                    for trace in traces
                    for model_run_id in model_run_ids_from_payload(trace.trace_payload)
                ],
            ]
        )

        counts = {
            "daily_analysis_jobs": self._delete_rows(jobs),
            "recommendations": self._delete_rows(recommendations),
            "readiness_assessments": self._delete_rows(assessments),
            "reasoning_traces": self._delete_rows(traces),
            "model_runs": self._delete_rows(
                [
                    run
                    for run_id in model_run_ids
                    if (run := self._session.get(ModelRun, run_id)) is not None
                    and run.user_id == user_id
                ]
            ),
            "memory_summaries": self._delete_memory_summaries_referencing_checkin(
                user_id=user_id,
                checkin_id=checkin_id,
            ),
        }
        return counts

    def _delete_note_derived_artifacts(
        self,
        *,
        user_id: UUID,
        checkin_id: UUID,
    ) -> dict[str, int]:
        rows = list(
            self._session.exec(select(MemorySummary).where(MemorySummary.user_id == user_id)).all()
        )
        return {
            "memory_summaries": self._delete_rows(
                [
                    row
                    for row in rows
                    if _contains_note_source_ref(row.source_refs, checkin_id)
                    or _contains_note_source_ref(row.observations, checkin_id)
                    or _contains_note_source_ref(row.hypotheses, checkin_id)
                ]
            )
        }

    def _delete_memory_summaries_referencing_checkin(
        self,
        *,
        user_id: UUID,
        checkin_id: UUID,
    ) -> int:
        rows = list(
            self._session.exec(select(MemorySummary).where(MemorySummary.user_id == user_id)).all()
        )
        return self._delete_rows(
            [
                row
                for row in rows
                if _contains_source_ref(row.source_refs, checkin_id)
                or _contains_source_ref(row.observations, checkin_id)
                or _contains_source_ref(row.hypotheses, checkin_id)
            ]
        )

    def _delete_rows(self, rows: list[Any]) -> int:
        seen: set[UUID] = set()
        deleted = 0
        for row in rows:
            row_id = row.id
            if row_id in seen:
                continue
            seen.add(row_id)
            self._session.delete(row)
            deleted += 1
        self._session.flush()
        return deleted

    def _delete_user_rows(self, model: type[Any], user_id: UUID) -> int:
        return self._delete_matching(select(model).where(model.user_id == user_id))

    def _delete_matching(self, statement: Any) -> int:
        rows = list(self._session.exec(statement).all())
        for row in rows:
            self._session.delete(row)
        self._session.flush()
        return len(rows)

    def _record_deletion_failure(
        self,
        *,
        event_type: AuditEventType,
        user_id: UUID,
        target: str,
        exc: Exception,
        target_id: UUID | None = None,
        rollback: bool = True,
    ) -> None:
        if rollback or not self._session.is_active:
            self._session.rollback()
        metadata: dict[str, Any] = {
            "target": target,
            "status": "failed",
            "error_code": type(exc).__name__,
        }
        if target_id is not None:
            metadata[_target_id_key(target)] = str(target_id)
        try:
            emit_privacy_audit(
                self._session,
                event_type=event_type,
                user_id=user_id,
                metadata=metadata,
            )
        except IntegrityError:
            self._session.rollback()
            emit_privacy_audit(
                self._session,
                event_type=event_type,
                user_id=None,
                metadata=metadata,
            )
        self._session.commit()


def _target_id_key(target: str) -> str:
    if target in {"checkin", "checkin_note"}:
        return "checkin_id"
    if target == "memory_summary":
        return "memory_summary_id"
    return f"{target}_id"


def _unique_ids(values: list[UUID | None]) -> list[UUID]:
    seen: set[UUID] = set()
    result: list[UUID] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _contains_source_ref(value: Any, checkin_id: UUID) -> bool:
    if isinstance(value, dict):
        if value.get("table") == "daily_check_in" and str(value.get("id")) == str(checkin_id):
            return True
        return any(_contains_source_ref(item, checkin_id) for item in value.values())
    if isinstance(value, list):
        return any(_contains_source_ref(item, checkin_id) for item in value)
    return False


def _contains_note_source_ref(value: Any, checkin_id: UUID) -> bool:
    if isinstance(value, dict):
        if value.get("table") == "daily_check_in" and str(value.get("id")) == str(checkin_id):
            return any(
                isinstance(value.get(key), str) and "note" in value[key]
                for key in ("field", "column", "source", "path")
            )
        return any(_contains_note_source_ref(item, checkin_id) for item in value.values())
    if isinstance(value, list):
        return any(_contains_note_source_ref(item, checkin_id) for item in value)
    return False
