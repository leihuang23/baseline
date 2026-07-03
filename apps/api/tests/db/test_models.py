"""Model insert/read, provenance, FK, and enum integrity tests."""

import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from baseline_api.db.models import (
    AuditEvent,
    ConsentRecord,
    DailyCheckIn,
    DerivedDailyFeature,
    EvaluationCase,
    Goal,
    KnowledgeSource,
    MemorySummary,
    ModelRun,
    NormalizedHealthMetric,
    NormalizedHealthMetricSourceSample,
    RawHealthSample,
    ReadinessAssessment,
    Recommendation,
    SleepSession,
    User,
    WorkoutSession,
)
from baseline_api.db.models.enums import (
    AuditEventType,
    ConfidenceLevel,
    GoalCategory,
    KnowledgeSourceType,
    MetricType,
    Modality,
    PeriodType,
    PrivacyMode,
    ReadinessState,
    RecommendationBand,
    RecommendationType,
    RedactionStatus,
    RunType,
    SafetyStatus,
    SensitiveNotePolicy,
    TimeHorizon,
    TrustLevel,
)


@pytest.fixture
def user(db_session):
    """Create a user in the test transaction."""
    u = User(privacy_mode=PrivacyMode.local_only, active_consent_version="v1")
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def model_run(db_session, user):
    """Create a model run record for recommendation FK tests."""
    run = ModelRun(
        user_id=user.id,
        run_type=RunType.daily_briefing,
        model_provider="test-provider",
        model_name="test-model",
        prompt_version="v1",
        input_hash="abc",
        output_hash="def",
        schema_version="v1",
        token_usage={"prompt": 10, "completion": 5},
        cost=0.001,
        latency_ms=120,
        safety_result={"passed": True},
    )
    db_session.add(run)
    db_session.flush()
    return run


def _now():
    return dt.datetime.now(dt.UTC)


def _today():
    return dt.date.today()


def test_insert_and_read_each_entity(db_session, user, model_run) -> None:
    """All PRD §15 entities can be persisted and read back."""
    entities = [
        ConsentRecord(
            user_id=user.id,
            consent_version="v1",
            health_categories_enabled=["heart_rate", "sleep"],
            timestamp=_now(),
        ),
        RawHealthSample(
            user_id=user.id,
            source_platform="Apple Health",
            source_sample_id="hk-123",
            sample_type=MetricType.heart_rate_variability,
            start_time=_now(),
            raw_value=45.2,
            raw_unit="ms",
            source_metadata={"source": "watch"},
            imported_at=_now(),
            import_batch_id=uuid4(),
        ),
        NormalizedHealthMetric(
            user_id=user.id,
            metric_type=MetricType.heart_rate_variability,
            start_time=_now(),
            value=45.0,
            unit="ms",
            source_sample_ids=[str(uuid4())],
            normalization_version="v1",
        ),
        WorkoutSession(
            user_id=user.id,
            start_time=_now(),
            modality=Modality.run,
            duration=3600.0,
            intensity_zone_distribution={"zone2": 1800},
            muscle_group_tags=["legs"],
            source_sample_ids=[str(uuid4())],
        ),
        SleepSession(
            user_id=user.id,
            start_time=_now(),
            duration=28800.0,
            sleep_stage_breakdown={"deep": 7200},
            source_sample_ids=[str(uuid4())],
        ),
        DailyCheckIn(
            user_id=user.id,
            date=_today(),
            energy_score=7,
            sensitive_note_policy=SensitiveNotePolicy.exclude_from_external_llm,
            structured_notes={"sleep": "good"},
        ),
        Goal(
            user_id=user.id,
            category=GoalCategory.vo2_max,
            priority=1,
            time_horizon=TimeHorizon.medium_term,
            success_metric="improve_5k_time",
            constraints={"max_sessions_per_week": 4},
        ),
        DerivedDailyFeature(
            user_id=user.id,
            date=_today(),
            feature_version="v1",
            sleep_features={"duration": 7.5},
            hrv_features={"baseline": 45.0},
            rhr_features={"baseline": 52.0},
            training_load_features={"acute": 300},
            recovery_features={"score": 75},
            goal_features={"vo2_max": "on_track"},
            data_quality={"completeness": 0.9},
            anomaly_flags=["stale_sleep"],
            computed_at=_now(),
            source_sample_ids=[str(uuid4())],
        ),
        ReadinessAssessment(
            user_id=user.id,
            date=_today(),
            assessment_version="v1",
            readiness_state=ReadinessState.moderate,
            recommendation_band=RecommendationBand.moderate,
            confidence=ConfidenceLevel.medium,
            uncertainty=["no soreness check-in"],
            evidence_items=[{"metric": "hrv", "value": 45.0}],
            risk_flags=["high_training_density"],
            goal_tradeoffs={"vo2_max": "prioritized"},
            reasoning_trace_id=uuid4(),
        ),
        Recommendation(
            user_id=user.id,
            date=_today(),
            recommendation_type=RecommendationType.training,
            recommendation_text="Moderate run today.",
            candidate_options=[{"option": "easy_run"}],
            evidence_refs=[{"feature": "hrv"}],
            safety_status=SafetyStatus.passed,
            model_run_id=model_run.id,
        ),
        MemorySummary(
            user_id=user.id,
            period_type=PeriodType.weekly,
            start_date=_today(),
            end_date=_today(),
            summary_version="v1",
            observations=[{"text": "sleep improved"}],
            hypotheses=[{"text": "earlier bedtime helps"}],
            source_refs=[{"table": "sleep_session"}],
            sensitive_fields_excluded=["free_text_note"],
        ),
        KnowledgeSource(
            title="Exercise Physiology Reference",
            source_type=KnowledgeSourceType.research_paper,
            ingested_at=_now(),
            version="v1",
            trust_level=TrustLevel.peer_reviewed,
        ),
        EvaluationCase(
            scenario_name="high_readiness",
            input_fixture={"hrv": 55},
            expected_properties={"band": "hard_training_ok"},
            actual_output={"band": "hard_training_ok"},
            pass_fail=True,
            evaluated_at=_now(),
        ),
        AuditEvent(
            user_id=user.id,
            event_type=AuditEventType.sync_completed,
            actor="test",
            timestamp=_now(),
            event_metadata={"records": 10},
            redaction_status=RedactionStatus.redacted,
        ),
    ]

    for entity in entities:
        db_session.add(entity)
    db_session.flush()

    for entity in entities:
        read = db_session.get(type(entity), entity.id)
        assert read is not None
        assert read.id == entity.id


def test_source_sample_id_provenance_round_trips(db_session, user) -> None:
    """UUID strings stored as JSONB provenance survive the round-trip."""
    sample_ids = [str(uuid4()), str(uuid4())]
    raw = RawHealthSample(
        user_id=user.id,
        source_platform="Apple Health",
        source_sample_id="hk-provenance",
        sample_type=MetricType.steps,
        start_time=_now(),
        raw_value=10000.0,
        raw_unit="count",
        imported_at=_now(),
        import_batch_id=uuid4(),
    )
    norm = NormalizedHealthMetric(
        user_id=user.id,
        metric_type=MetricType.steps,
        start_time=_now(),
        value=10000.0,
        unit="count",
        source_sample_ids=sample_ids,
        normalization_version="v1",
    )
    db_session.add_all([raw, norm])
    db_session.flush()

    read_norm = db_session.get(NormalizedHealthMetric, norm.id)
    assert read_norm is not None
    assert read_norm.source_sample_ids == sample_ids
    assert all(isinstance(v, str) for v in read_norm.source_sample_ids)


def test_provenance_link_requires_existing_raw_sample(db_session, user) -> None:
    """FK-backed provenance rejects references to missing raw samples."""

    norm = NormalizedHealthMetric(
        user_id=user.id,
        metric_type=MetricType.steps,
        start_time=_now(),
        value=10000.0,
        unit="count",
        source_sample_ids=[],
        normalization_version="v1",
    )
    db_session.add(norm)
    db_session.flush()

    db_session.add(
        NormalizedHealthMetricSourceSample(
            normalized_health_metric_id=norm.id,
            raw_health_sample_id=uuid4(),
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_fk_integrity_rejects_missing_user(db_session) -> None:
    """Inserting a record referencing a non-existent user raises IntegrityError."""
    checkin = DailyCheckIn(
        user_id=uuid4(),
        date=_today(),
        sensitive_note_policy=SensitiveNotePolicy.exclude_from_external_llm,
    )
    db_session.add(checkin)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enum_rejects_invalid_value(db_session, user) -> None:
    """Native Postgres enums reject values outside the closed set."""
    with pytest.raises(DataError):
        db_session.execute(
            text(
                "INSERT INTO raw_health_sample "
                "(id, user_id, source_platform, source_sample_id, sample_type, "
                "start_time, raw_value, raw_unit, source_metadata, imported_at, "
                "import_batch_id, created_at, updated_at) "
                "VALUES (:id, :user_id, :platform, :sample_id, 'not_a_metric', "
                ":start_time, :value, :unit, '{}', :imported_at, :batch_id, "
                ":created_at, :updated_at)"
            ),
            {
                "id": str(uuid4()),
                "user_id": str(user.id),
                "platform": "Apple Health",
                "sample_id": "hk-bad",
                "start_time": _now(),
                "value": 1.0,
                "unit": "count",
                "imported_at": _now(),
                "batch_id": str(uuid4()),
                "created_at": _now(),
                "updated_at": _now(),
            },
        )
        db_session.flush()
