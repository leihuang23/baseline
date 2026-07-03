"""CLI for running registered Baseline eval suites."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlmodel import Session, create_engine

from baseline_api.config import get_settings
from packages.eval.reporters import write_reports
from packages.eval.runner import EvalRunner, gate_failed
from packages.eval.suites import build_default_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Baseline evaluation suites.")
    parser.add_argument(
        "--suite",
        action="append",
        dest="suite_names",
        help="Suite name to run. Repeat to run multiple suites. Defaults to all registered suites.",
    )
    parser.add_argument(
        "--report-dir",
        default="artifacts/eval",
        help="Directory for evaluation-report.json and evaluation-report.md.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = create_engine(str(settings.database_url))
    registry = build_default_registry()
    report_dir = Path(args.report_dir)

    with Session(engine) as session:
        runner = EvalRunner(registry, session)
        run_result = runner.run(args.suite_names)
        session.commit()

    write_reports(run_result, report_dir)
    return 1 if gate_failed(run_result) else 0
