from pathlib import Path


def test_healthkit_feasibility_doc_closes_p0_exit_criteria() -> None:
    doc = Path("docs/architecture/healthkit-feasibility.md").read_text(encoding="utf-8")

    for required in [
        "no unresolved HealthKit data-access blocker",
        "Sleep",
        "Workouts",
        "Steps",
        "HRV",
        "Resting heart rate",
        "VO2 max",
        "anchored incremental reads",
        "partial permissions",
        "HealthSyncRequest",
    ]:
        assert required in doc
