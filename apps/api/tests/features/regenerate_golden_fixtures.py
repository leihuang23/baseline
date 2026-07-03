"""Regenerate expected_golden_outputs.json after intentional formula changes.

Run with:
    uv run python apps/api/tests/features/regenerate_golden_fixtures.py
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from golden_fixtures import FIXTURES

from baseline_api.features.assembler import assemble_daily_features
from baseline_api.features.cardio import compute_hrv_features, compute_rhr_features
from baseline_api.features.sleep import compute_sleep_features
from baseline_api.features.training_load import compute_training_load_features, compute_vo2_features


def _jsonify(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    return value


def main() -> None:
    expected: dict[str, dict[str, object]] = {}
    for fixture in FIXTURES.values():
        sleep = compute_sleep_features(
            fixture.target_date,
            fixture.sleep_sessions,
            personal_sleep_need_hours=fixture.personal_sleep_need_hours,
        )
        hrv = compute_hrv_features(fixture.target_date, fixture.hrv_samples)
        rhr = compute_rhr_features(fixture.target_date, fixture.rhr_samples)
        training_load = compute_training_load_features(fixture.target_date, fixture.workouts)
        vo2 = compute_vo2_features(fixture.target_date, fixture.vo2_samples)
        bundle = assemble_daily_features(
            fixture.target_date,
            sleep_sessions=fixture.sleep_sessions,
            hrv_samples=fixture.hrv_samples,
            rhr_samples=fixture.rhr_samples,
            workouts=fixture.workouts,
            vo2_samples=fixture.vo2_samples,
            personal_sleep_need_hours=fixture.personal_sleep_need_hours,
            computed_at=dt.datetime(2026, 1, 20, 8, 0, 0, tzinfo=dt.UTC),
        )
        expected[fixture.name] = {
            "sleep": sleep,
            "hrv": hrv,
            "rhr": rhr,
            "training_load": training_load,
            "vo2": vo2,
            "recovery": bundle.recovery_features,
            "goal": bundle.goal_features,
            "bundle": bundle.to_derived_daily_feature_fields(),
        }

    output_path = (
        Path(__file__).with_suffix("").parent / "fixtures" / "expected_golden_outputs.json"
    )
    output_path.write_text(
        json.dumps(_jsonify(expected), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
