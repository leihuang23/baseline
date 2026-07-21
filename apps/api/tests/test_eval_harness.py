"""Tests for the evaluation harness."""

from pathlib import Path
from types import SimpleNamespace

from packages.eval import EvalRegistry, EvalRunner, EvalSuite, EvalType
from packages.eval.cli import main as eval_main
from packages.eval.reporters import write_reports
from packages.eval.runner import gate_failed
from packages.eval.scorers import expected_outcomes_match
from sqlmodel import select

from baseline_api.db.models.evaluation import EvaluationCase


def test_harness_persists_pass_fail_and_reports(db_session, tmp_path: Path) -> None:
    """Passing and failing suites persist rows and render failure reasons."""

    registry = EvalRegistry()
    registry.register(
        EvalSuite(
            name="passing_readiness",
            eval_type=EvalType.DETERMINISTIC,
            scenario_name="high_hrv_good_sleep_low_load",
            input_fixture="high_hrv_good_sleep_low_load",
            expected_properties={"readiness": "high"},
            scorer=expected_outcomes_match,
        )
    )
    registry.register(
        EvalSuite(
            name="failing_readiness",
            eval_type=EvalType.DETERMINISTIC,
            scenario_name="high_hrv_good_sleep_low_load",
            input_fixture="high_hrv_good_sleep_low_load",
            expected_properties={"readiness": "low"},
            scorer=expected_outcomes_match,
        )
    )

    run_result = EvalRunner(registry, db_session).run()
    json_path, markdown_path = write_reports(run_result, tmp_path)

    assert [result.passed for result in run_result.results] == [False, True]
    assert gate_failed(run_result) is False

    cases = db_session.exec(select(EvaluationCase)).all()
    assert len(cases) == 2
    assert {case.pass_fail for case in cases} == {False, True}
    assert any(case.failure_reason for case in cases)
    assert all(case.actual_output["eval_type"] == "deterministic" for case in cases)

    json_report = json_path.read_text(encoding="utf-8")
    markdown_report = markdown_path.read_text(encoding="utf-8")
    assert '"deterministic"' in json_report
    assert "failing_readiness" in json_report
    assert "Expected outcome mismatches" in markdown_report


def test_safety_eval_failure_trips_ci_gate(db_session, tmp_path: Path, monkeypatch) -> None:
    """A failing safety suite exits nonzero for the CI gate."""

    registry = EvalRegistry()
    registry.register(
        EvalSuite(
            name="failing_medical_boundary",
            eval_type=EvalType.SAFETY,
            scenario_name="medical_diagnosis_request",
            input_fixture="medical_diagnosis_request",
            expected_properties={"safety_status": "passed"},
            scorer=expected_outcomes_match,
        )
    )

    def build_registry() -> EvalRegistry:
        return registry

    class NoCommitSession:
        def __init__(self, session):
            self.session = session

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, instance):
            self.session.add(instance)

        def flush(self):
            self.session.flush()

        def commit(self):
            return None

    monkeypatch.setattr("packages.eval.cli.build_default_registry", build_registry)
    monkeypatch.setattr(
        "packages.eval.cli.get_settings",
        lambda: SimpleNamespace(database_url="postgresql+psycopg://baseline@example/baseline"),
    )
    monkeypatch.setattr("packages.eval.cli.create_engine", lambda _: object())
    monkeypatch.setattr("packages.eval.cli.Session", lambda _: NoCommitSession(db_session))

    exit_code = eval_main(["--report-dir", str(tmp_path)])

    assert exit_code == 1
    assert (tmp_path / "evaluation-report.json").exists()
    assert "failing_medical_boundary" in (tmp_path / "evaluation-report.md").read_text(
        encoding="utf-8"
    )


def test_ci_migrates_database_before_eval_gate() -> None:
    """The CI eval gate runs against a migrated service database."""

    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    migrate_index = workflow.find("run: make migrate")
    eval_index = workflow.find("run: make eval")

    assert migrate_index != -1
    assert eval_index != -1
    assert migrate_index < eval_index
