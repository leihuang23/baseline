"""Persistence and retrieval service for structured memory summaries."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlmodel import Session

from baseline_api.db.models.assessment import ReadinessAssessment, Recommendation
from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.enums import AuditEventType, PeriodType, RedactionStatus
from baseline_api.db.models.features import DerivedDailyFeature
from baseline_api.db.models.memory import MemorySummary
from baseline_api.db.repositories.audit import AuditEventRepository
from baseline_api.db.repositories.memory import MemorySummaryRepository
from baseline_api.memory.compiler import MemoryCompiler

JsonDict = dict[str, Any]


class MemoryService:
    """Compile, persist, correct, delete, and retrieve memory summaries."""

    def __init__(
        self,
        session: Session,
        *,
        compiler: MemoryCompiler | None = None,
    ) -> None:
        self._session = session
        self._compiler = compiler or MemoryCompiler()
        self._summaries = MemorySummaryRepository(session)
        self._audits = AuditEventRepository(session)

    def generate_daily_summary(
        self,
        *,
        user_id: UUID,
        feature: DerivedDailyFeature,
        assessment: ReadinessAssessment,
        recommendation: Recommendation | None = None,
        checkin: DailyCheckIn | None = None,
        include_sensitive_notes: bool = False,
        commit: bool = True,
    ) -> MemorySummary:
        """Compile and upsert a daily memory summary."""

        summary = self._compiler.compile_daily(
            user_id=user_id,
            feature=feature,
            assessment=assessment,
            recommendation=recommendation,
            checkin=checkin,
            include_sensitive_notes=include_sensitive_notes,
        )
        persisted = self._upsert_summary(summary)
        if commit:
            self._session.commit()
        return persisted

    def generate_weekly_summary(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
        commit: bool = True,
    ) -> MemorySummary:
        """Compile and upsert a weekly summary from persisted daily summaries."""

        dailies = self._summaries.daily_between(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )
        summary = self._compiler.compile_weekly(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            daily_summaries=dailies,
        )
        persisted = self._upsert_summary(summary)
        if commit:
            self._session.commit()
        return persisted

    def generate_monthly_summary(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
        commit: bool = True,
    ) -> MemorySummary:
        """Compile and upsert a monthly summary from persisted daily/weekly summaries."""

        dailies = self._summaries.daily_between(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )
        weeklies = self._summaries.weekly_between(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )
        summary = self._compiler.compile_monthly(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            daily_summaries=dailies,
            weekly_summaries=weeklies,
        )
        persisted = self._upsert_summary(summary)
        if commit:
            self._session.commit()
        return persisted

    def generate_quarterly_summary(
        self,
        *,
        user_id: UUID,
        start_date: dt.date,
        end_date: dt.date,
        commit: bool = True,
    ) -> MemorySummary:
        """Compile and upsert a quarterly summary from persisted monthly summaries."""

        monthlies = self._summaries.monthly_between(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )
        summary = self._compiler.compile_quarterly(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            monthly_summaries=monthlies,
        )
        persisted = self._upsert_summary(summary)
        if commit:
            self._session.commit()
        return persisted

    def list_summaries(
        self,
        *,
        user_id: UUID,
        period_type: PeriodType | None = None,
        limit: int = 100,
    ) -> list[MemorySummary]:
        return self._summaries.list_for_user(
            user_id=user_id,
            period_type=period_type,
            limit=limit,
        )

    def recent_for_reasoning(
        self,
        *,
        user_id: UUID,
        target_date: dt.date,
        limit: int = 5,
    ) -> list[JsonDict]:
        """Return compact summaries for reasoning before any raw history lookup."""

        summaries = self._summaries.recent_before(
            user_id=user_id,
            before_date=target_date,
            limit=limit,
        )
        return [_summary_for_reasoning(summary) for summary in summaries]

    def correct_summary(
        self,
        summary_id: UUID,
        *,
        observations: Sequence[Mapping[str, Any]] | None = None,
        hypotheses: Sequence[Mapping[str, Any]] | None = None,
        actor: str = "user",
    ) -> MemorySummary:
        """Replace corrected structured items and emit a redacted audit event."""

        summary = self._summaries.get_by_id(summary_id)
        if summary is None:
            raise ValueError("memory summary not found")

        changed_fields: list[str] = []
        if observations is not None:
            summary.observations = _validated_items(observations, kind="observation")
            changed_fields.append("observations")
        if hypotheses is not None:
            summary.hypotheses = _validated_items(hypotheses, kind="hypothesis")
            changed_fields.append("hypotheses")
        if not changed_fields:
            raise ValueError("at least one corrected field is required")

        summary.confidence = _aggregate_confidence(summary.observations, summary.hypotheses)
        summary.source_refs = _source_refs_from_items(
            summary.source_refs,
            summary.observations,
            summary.hypotheses,
        )
        summary.updated_at = dt.datetime.now(dt.UTC)
        self._session.add(summary)
        self._emit_audit(
            AuditEventType.memory_corrected,
            user_id=summary.user_id,
            actor=actor,
            metadata={
                "memory_summary_id": str(summary.id),
                "period_type": summary.period_type.value,
                "start_date": summary.start_date.isoformat(),
                "end_date": summary.end_date.isoformat(),
                "changed_fields": changed_fields,
                "source_refs": summary.source_refs,
            },
        )
        self._session.commit()
        return summary

    def delete_summary(self, summary_id: UUID, *, actor: str = "user") -> None:
        """Delete a memory summary and emit a redacted audit event."""

        summary = self._summaries.get_by_id(summary_id)
        if summary is None:
            raise ValueError("memory summary not found")

        metadata = {
            "memory_summary_id": str(summary.id),
            "period_type": summary.period_type.value,
            "start_date": summary.start_date.isoformat(),
            "end_date": summary.end_date.isoformat(),
            "source_refs": summary.source_refs,
        }
        user_id = summary.user_id
        self._session.delete(summary)
        self._emit_audit(
            AuditEventType.memory_deleted,
            user_id=user_id,
            actor=actor,
            metadata=metadata,
        )
        self._session.commit()

    def _upsert_summary(self, summary: MemorySummary) -> MemorySummary:
        existing = self._summaries.latest_for_period(
            user_id=summary.user_id,
            period_type=summary.period_type,
            start_date=summary.start_date,
            end_date=summary.end_date,
        )
        if existing is None:
            return self._summaries.create(summary)

        existing.summary_version = summary.summary_version
        existing.observations = summary.observations
        existing.hypotheses = summary.hypotheses
        existing.confidence = summary.confidence
        existing.source_refs = summary.source_refs
        existing.sensitive_fields_excluded = summary.sensitive_fields_excluded
        existing.updated_at = dt.datetime.now(dt.UTC)
        self._session.add(existing)
        self._session.flush()
        return existing

    def _emit_audit(
        self,
        event_type: AuditEventType,
        *,
        user_id: UUID,
        actor: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self._audits.create(
            AuditEvent(
                user_id=user_id,
                event_type=event_type,
                actor=actor,
                timestamp=dt.datetime.now(dt.UTC),
                event_metadata=dict(metadata),
                redaction_status=RedactionStatus.redacted,
            )
        )


def _summary_for_reasoning(summary: MemorySummary) -> JsonDict:
    observation_texts = [
        str(item["text"]) for item in summary.observations if isinstance(item.get("text"), str)
    ]
    hypothesis_texts = [
        str(item["text"]) for item in summary.hypotheses if isinstance(item.get("text"), str)
    ]
    return {
        "memory_summary_id": str(summary.id),
        "period_type": summary.period_type.value,
        "period": _period_label(summary),
        "summary_version": summary.summary_version,
        "confidence": summary.confidence,
        "observation": " ".join(observation_texts[:3]),
        "observations": summary.observations,
        "hypotheses": summary.hypotheses,
        "hypothesis": " ".join(hypothesis_texts[:2]),
        "source_refs": summary.source_refs,
    }


def _period_label(summary: MemorySummary) -> str:
    if summary.start_date == summary.end_date:
        return summary.start_date.isoformat()
    return f"{summary.start_date.isoformat()}..{summary.end_date.isoformat()}"


def _validated_items(items: Sequence[Mapping[str, Any]], *, kind: str) -> list[JsonDict]:
    validated: list[JsonDict] = []
    for item in items:
        if item.get("kind") != kind:
            raise ValueError(f"{kind} item has invalid kind")
        if not isinstance(item.get("text"), str) or not item["text"]:
            raise ValueError(f"{kind} item requires text")
        confidence = item.get("confidence")
        if not isinstance(confidence, int | float) or isinstance(confidence, bool):
            raise ValueError(f"{kind} item requires confidence")
        refs = item.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"{kind} item requires source_refs")
        if not all(isinstance(ref, Mapping) and isinstance(ref.get("table"), str) for ref in refs):
            raise ValueError(f"{kind} item requires structured source_refs")
        validated_item = {str(key): _jsonable(value) for key, value in item.items()}
        validated_item["confidence"] = _bounded_confidence(float(confidence))
        validated.append(validated_item)
    return validated


def _source_refs_from_items(
    *groups: Sequence[Mapping[str, Any]],
) -> list[JsonDict]:
    seen: set[str] = set()
    refs: list[JsonDict] = []
    for group in groups:
        for item in group:
            raw_refs = item.get("source_refs") if isinstance(item, Mapping) else None
            if not isinstance(raw_refs, list):
                raw_refs = [item]
            for raw_ref in raw_refs:
                if not isinstance(raw_ref, Mapping):
                    continue
                ref = {str(key): _jsonable(value) for key, value in raw_ref.items()}
                marker = str(sorted(ref.items()))
                if marker in seen:
                    continue
                seen.add(marker)
                refs.append(ref)
    return refs


def _bounded_confidence(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def _aggregate_confidence(
    observations: Sequence[Mapping[str, Any]],
    hypotheses: Sequence[Mapping[str, Any]],
) -> float:
    values = [
        float(item["confidence"])
        for item in [*observations, *hypotheses]
        if isinstance(item.get("confidence"), int | float)
    ]
    if not values:
        return 0.0
    return round(max(0.0, min(1.0, sum(values) / len(values))), 3)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(raw) for key, raw in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dt.date | dt.datetime | UUID):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    return value
