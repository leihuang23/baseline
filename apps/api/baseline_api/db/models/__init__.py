"""SQLModel table definitions for Baseline."""

from baseline_api.db.models.assessment import ReadinessAssessment, Recommendation
from baseline_api.db.models.audit import AuditEvent
from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.evaluation import EvaluationCase
from baseline_api.db.models.features import DerivedDailyFeature
from baseline_api.db.models.goals import Goal
from baseline_api.db.models.ingestion import NormalizedHealthMetric, RawHealthSample
from baseline_api.db.models.knowledge import KnowledgeSource
from baseline_api.db.models.memory import MemorySummary
from baseline_api.db.models.modelrun import ModelRun
from baseline_api.db.models.sessions import SleepSession, WorkoutSession
from baseline_api.db.models.user import ConsentRecord, User

__all__ = [
    "AuditEvent",
    "ConsentRecord",
    "DailyCheckIn",
    "DerivedDailyFeature",
    "EvaluationCase",
    "Goal",
    "KnowledgeSource",
    "MemorySummary",
    "ModelRun",
    "NormalizedHealthMetric",
    "RawHealthSample",
    "ReadinessAssessment",
    "Recommendation",
    "SleepSession",
    "User",
    "WorkoutSession",
]
