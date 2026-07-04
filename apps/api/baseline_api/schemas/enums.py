"""Closed-set values used by API contracts."""

from enum import StrEnum


class AnalysisJobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class ConfidenceLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class DataExportFormat(StrEnum):
    json = "json"
    csv = "csv"


class DataExportScope(StrEnum):
    all = "all"
    health = "health"
    checkins = "checkins"
    briefings = "briefings"
    recommendations = "recommendations"


class DataExportStatus(StrEnum):
    queued = "queued"
    running = "running"
    ready = "ready"
    failed = "failed"
    expired = "expired"


class DataScope(StrEnum):
    briefing_trace = "briefing_trace"
    recent_health = "recent_health"
    checkins = "checkins"
    memory = "memory"
    goals = "goals"
    external_knowledge = "external_knowledge"


class DataQualitySeverity(StrEnum):
    info = "info"
    warning = "warning"
    degraded = "degraded"


class EvalQueueStatus(StrEnum):
    queued = "queued"
    skipped = "skipped"
    failed = "failed"


class FeedbackActionTaken(StrEnum):
    followed = "followed"
    partially_followed = "partially_followed"
    ignored = "ignored"
    planned = "planned"


class FeedbackRating(StrEnum):
    useful = "useful"
    somewhat_useful = "somewhat_useful"
    not_useful = "not_useful"
    unsafe_or_wrong = "unsafe_or_wrong"


class MemoryUpdateStatus(StrEnum):
    queued = "queued"
    applied = "applied"
    skipped = "skipped"
    failed = "failed"


class MetricType(StrEnum):
    heart_rate_variability = "heart_rate_variability"
    resting_heart_rate = "resting_heart_rate"
    steps = "steps"
    active_energy = "active_energy"
    vo2_max = "vo2_max"
    blood_oxygen = "blood_oxygen"
    body_temperature = "body_temperature"
    sleep_duration = "sleep_duration"
    workout = "workout"
    other = "other"


class PrivacyMode(StrEnum):
    local_only = "local_only"
    cloud_assisted = "cloud_assisted"
    hybrid = "hybrid"


class ReadinessState(StrEnum):
    high = "high"
    moderate = "moderate"
    low = "low"
    mixed = "mixed"
    insufficient_data = "insufficient_data"


class RecommendationBand(StrEnum):
    hard_training_ok = "hard_training_ok"
    moderate = "moderate"
    moderate_or_upper_body = "moderate_or_upper_body"
    easy = "easy"
    easy_or_recovery = "easy_or_recovery"
    recovery = "recovery"
    rest = "rest"
    insufficient_data = "insufficient_data"


class RedactionStatus(StrEnum):
    redacted = "redacted"
    partial = "partial"
    none = "none"


class GoalCategory(StrEnum):
    cognitive_performance = "cognitive_performance"
    vo2_max = "vo2_max"
    strength = "strength"
    recovery = "recovery"
    sleep = "sleep"
    long_term_wellness = "long_term_wellness"


class GoalTimeHorizon(StrEnum):
    short_term = "short_term"
    medium_term = "medium_term"
    long_term = "long_term"


class SafetyStatus(StrEnum):
    passed = "passed"
    blocked = "blocked"
    rewritten = "rewritten"
    escalated = "escalated"


class SensitiveNotePolicy(StrEnum):
    exclude_from_external_llm = "exclude_from_external_llm"
    summarize_before_external_llm = "summarize_before_external_llm"
    allow_external_llm = "allow_external_llm"
