"""Tests for synthetic fixture generation and loading."""

from collections import Counter

import pytest
from packages.fixtures import (
    GOLDEN_SCENARIO_NAMES,
    PersonaConfig,
    emit_raw_sync_payload,
    fixture_to_json_bytes,
    generate_persona_dataset,
    get_scenario,
    list_scenarios,
    load_fixture,
)
from sqlmodel import select

from baseline_api.db.models.checkin import DailyCheckIn
from baseline_api.db.models.ingestion import RawHealthSample
from baseline_api.db.models.sessions import SleepSession, WorkoutSession
from baseline_api.schemas.api import HealthSyncRequest


def test_same_seed_produces_byte_identical_output() -> None:
    """The serialized dataset is reproducible for a given seed."""

    config = PersonaConfig(seed=202, days=30, name="determinism_check")
    first = fixture_to_json_bytes(generate_persona_dataset(config))
    second = fixture_to_json_bytes(generate_persona_dataset(config))

    assert first == second


def test_different_seed_changes_output() -> None:
    """The seed controls generated values."""

    first = fixture_to_json_bytes(generate_persona_dataset(PersonaConfig(seed=202, days=7)))
    second = fixture_to_json_bytes(generate_persona_dataset(PersonaConfig(seed=203, days=7)))

    assert first != second


def test_generated_metric_ranges_and_units_are_normalizer_ready() -> None:
    """Generated samples stay inside realistic ranges with expected units."""

    dataset = generate_persona_dataset(PersonaConfig(seed=303, days=60))
    samples_by_type = {sample.metric_type: [] for sample in dataset.samples}
    for sample in dataset.samples:
        samples_by_type[sample.metric_type].append(sample)

    assert {sample.unit for sample in samples_by_type["heart_rate_variability"]} == {"ms"}
    assert all(18 <= sample.value <= 105 for sample in samples_by_type["heart_rate_variability"])
    assert {sample.unit for sample in samples_by_type["resting_heart_rate"]} == {"count/min"}
    assert all(38 <= sample.value <= 95 for sample in samples_by_type["resting_heart_rate"])
    assert {sample.unit for sample in samples_by_type["steps"]} == {"count"}
    assert all(1800 <= sample.value <= 26000 for sample in samples_by_type["steps"])
    assert {sample.unit for sample in samples_by_type["vo2_max"]} == {"mL/kg/min"}
    assert all(25 <= sample.value <= 70 for sample in samples_by_type["vo2_max"])
    assert {sample.unit for sample in samples_by_type["sleep_duration"]} == {"h"}
    assert all(4.2 <= sample.value <= 9.4 for sample in samples_by_type["sleep_duration"])

    assert all(0 <= sleep.quality_proxy <= 1 for sleep in dataset.sleep_sessions)
    assert all(
        4.2 * 3600 <= sleep.duration_seconds <= 9.4 * 3600 for sleep in dataset.sleep_sessions
    )
    assert all(
        workout.modality in {"run", "kettlebell", "strength"} for workout in dataset.workouts
    )
    assert all(1 <= checkin.energy_score <= 10 for checkin in dataset.checkins)
    assert all(checkin.structured_notes.get("synthetic") is True for checkin in dataset.checkins)


def test_catalog_contains_all_golden_scenarios_and_eval_breadth() -> None:
    """The catalog contains the PRD golden scenarios plus enough synthetic breadth."""

    scenario_names = set(list_scenarios())

    assert set(GOLDEN_SCENARIO_NAMES).issubset(scenario_names)
    assert len(scenario_names) >= 30


def test_named_scenario_shapes_match_expected_perturbations() -> None:
    """Scenario records expose the data gaps and labels downstream evals need."""

    demo = get_scenario("demo_60_day_persona")
    missing_hrv = get_scenario("missing_hrv")
    stale_sleep = get_scenario("stale_sleep")
    lower_body = get_scenario("three_lower_body_sessions_six_days")
    illness = get_scenario("illness_flag_high_motivation")

    assert demo.days == 60
    assert demo.expected_outcomes["contains_real_pii"] is False
    assert all(checkin.free_text_note_reference is None for checkin in demo.checkins)

    assert "golden" in missing_hrv.labels
    sample_counts = Counter(sample.metric_type for sample in missing_hrv.samples)
    assert sample_counts["heart_rate_variability"] < missing_hrv.days

    assert len(stale_sleep.sleep_sessions) < stale_sleep.days
    assert sum("lower_body" in workout.muscle_group_tags for workout in lower_body.workouts) >= 3
    assert any(
        checkin.illness_flag
        and checkin.energy_score >= 7
        and checkin.structured_notes["motivation"] == "high"
        for checkin in illness.checkins
    )


def test_raw_sync_payload_is_synthetic_and_contract_shaped() -> None:
    """Raw sync emission contains only synthetic HealthKit-like payloads."""

    dataset = get_scenario("high_hrv_good_sleep_low_load")
    payload = emit_raw_sync_payload(dataset)

    contract = HealthSyncRequest.model_validate(payload)

    assert contract.client_sync_id.startswith("synthetic:high_hrv_good_sleep_low_load")
    assert contract.device_id == "baseline-synthetic-watch"
    assert contract.consent_version == "synthetic-v1"
    assert payload["samples"]
    assert all(sample["source_metadata"]["synthetic"] is True for sample in payload["samples"])


@pytest.mark.parametrize("scenario_name", GOLDEN_SCENARIO_NAMES)
def test_each_golden_scenario_loads_into_db(db_session, scenario_name: str) -> None:
    """Every PRD golden scenario can be inserted into the P0-02 schema."""

    dataset = get_scenario(scenario_name)
    loaded = load_fixture(db_session, dataset)

    assert loaded.raw_sample_count == len(dataset.samples) + len(dataset.workouts)
    assert loaded.normalized_metric_count == len(dataset.samples)
    assert loaded.workout_count == len(dataset.workouts)
    assert loaded.sleep_count == len(dataset.sleep_sessions)
    assert loaded.checkin_count == len(dataset.checkins)

    raw_count = len(
        db_session.exec(
            select(RawHealthSample).where(RawHealthSample.user_id == loaded.user.id)
        ).all()
    )
    workout_count = len(
        db_session.exec(
            select(WorkoutSession).where(WorkoutSession.user_id == loaded.user.id)
        ).all()
    )
    sleep_count = len(
        db_session.exec(select(SleepSession).where(SleepSession.user_id == loaded.user.id)).all()
    )
    checkin_count = len(
        db_session.exec(select(DailyCheckIn).where(DailyCheckIn.user_id == loaded.user.id)).all()
    )

    assert raw_count == len(dataset.samples) + len(dataset.workouts)
    assert workout_count == len(dataset.workouts)
    assert sleep_count == len(dataset.sleep_sessions)
    assert checkin_count == len(dataset.checkins)
