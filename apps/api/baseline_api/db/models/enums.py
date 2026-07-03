"""Closed-set enumerations used by the data model.

No free strings are allowed for these domains at the database layer.
"""

from enum import StrEnum


class PrivacyMode(StrEnum):
    local_only = "local_only"
    cloud_assisted = "cloud_assisted"
    hybrid = "hybrid"


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


class Modality(StrEnum):
    run = "run"
    walk = "walk"
    cycle = "cycle"
    swim = "swim"
    strength = "strength"
    kettlebell = "kettlebell"
    yoga = "yoga"
    mobility = "mobility"
    hiit = "hiit"
    team_sport = "team_sport"
    other = "other"


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


class ConfidenceLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class PeriodType(StrEnum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    quarterly = "quarterly"


class TrustLevel(StrEnum):
    peer_reviewed = "peer_reviewed"
    authoritative = "authoritative"
    curated = "curated"
    unverified = "unverified"


class SensitiveNotePolicy(StrEnum):
    exclude_from_external_llm = "exclude_from_external_llm"
    summarize_before_external_llm = "summarize_before_external_llm"
    allow_external_llm = "allow_external_llm"


class GoalCategory(StrEnum):
    cognitive_performance = "cognitive_performance"
    vo2_max = "vo2_max"
    strength = "strength"
    recovery = "recovery"
    sleep = "sleep"
    long_term_wellness = "long_term_wellness"


class TimeHorizon(StrEnum):
    short_term = "short_term"
    medium_term = "medium_term"
    long_term = "long_term"


class RecommendationType(StrEnum):
    training = "training"
    recovery = "recovery"
    lifestyle = "lifestyle"
    follow_up_question = "follow_up_question"


class SafetyStatus(StrEnum):
    passed = "passed"
    blocked = "blocked"
    rewritten = "rewritten"
    escalated = "escalated"


class KnowledgeSourceType(StrEnum):
    research_paper = "research_paper"
    book = "book"
    article = "article"
    guideline = "guideline"
    dataset = "dataset"


class RunType(StrEnum):
    daily_briefing = "daily_briefing"
    explanation = "explanation"
    memory_summary = "memory_summary"
    follow_up = "follow_up"
    safety_check = "safety_check"


class AuditEventType(StrEnum):
    consent_granted = "consent_granted"
    consent_revoked = "consent_revoked"
    data_export_requested = "data_export_requested"
    data_deleted = "data_deleted"
    sync_completed = "sync_completed"
    recommendation_viewed = "recommendation_viewed"
    feedback_submitted = "feedback_submitted"
    model_run_logged = "model_run_logged"
    safety_flag_triggered = "safety_flag_triggered"


class RedactionStatus(StrEnum):
    redacted = "redacted"
    partial = "partial"
    none = "none"
