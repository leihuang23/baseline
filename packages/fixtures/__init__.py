"""Synthetic fixture generators for Baseline.

All generated data is synthetic, deterministic for a given seed, and safe for
public demo use.
"""

from packages.fixtures.generators import PersonaConfig, generate_persona_dataset
from packages.fixtures.loaders import emit_raw_sync_payload, load_fixture
from packages.fixtures.models import (
    CheckInRecord,
    FixtureDataset,
    HealthSample,
    SleepRecord,
    WorkoutRecord,
    fixture_to_json_bytes,
)
from packages.fixtures.scenarios import GOLDEN_SCENARIO_NAMES, get_scenario, list_scenarios

__all__ = [
    "CheckInRecord",
    "FixtureDataset",
    "GOLDEN_SCENARIO_NAMES",
    "HealthSample",
    "PersonaConfig",
    "SleepRecord",
    "WorkoutRecord",
    "emit_raw_sync_payload",
    "fixture_to_json_bytes",
    "generate_persona_dataset",
    "get_scenario",
    "list_scenarios",
    "load_fixture",
]
