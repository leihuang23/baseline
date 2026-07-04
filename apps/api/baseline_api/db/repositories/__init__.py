"""Thin repository stubs for Baseline data access.

These are intentionally minimal; later slices will expand them as business-logic
needs become clear.
"""

from baseline_api.db.repositories.assessment import (
    ReadinessAssessmentRepository,
    RecommendationRepository,
)
from baseline_api.db.repositories.audit import AuditEventRepository
from baseline_api.db.repositories.checkin import DailyCheckInRepository
from baseline_api.db.repositories.evaluation import EvaluationCaseRepository
from baseline_api.db.repositories.features import DerivedDailyFeatureRepository
from baseline_api.db.repositories.goals import GoalRepository
from baseline_api.db.repositories.ingestion import (
    BackfillJobRepository,
    DailyDataQualityRepository,
    HealthImportBatchRepository,
    NormalizedHealthMetricRepository,
    RawHealthSampleRepository,
)
from baseline_api.db.repositories.knowledge import (
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
)
from baseline_api.db.repositories.memory import MemorySummaryRepository
from baseline_api.db.repositories.modelrun import ModelRunRepository
from baseline_api.db.repositories.sessions import (
    SleepSessionRepository,
    WorkoutSessionRepository,
)
from baseline_api.db.repositories.user import ConsentRecordRepository, UserRepository

__all__ = [
    "AuditEventRepository",
    "BackfillJobRepository",
    "ConsentRecordRepository",
    "DailyDataQualityRepository",
    "DailyCheckInRepository",
    "DerivedDailyFeatureRepository",
    "EvaluationCaseRepository",
    "GoalRepository",
    "HealthImportBatchRepository",
    "KnowledgeSourceRepository",
    "KnowledgeChunkRepository",
    "MemorySummaryRepository",
    "ModelRunRepository",
    "NormalizedHealthMetricRepository",
    "RawHealthSampleRepository",
    "ReadinessAssessmentRepository",
    "RecommendationRepository",
    "SleepSessionRepository",
    "UserRepository",
    "WorkoutSessionRepository",
]
