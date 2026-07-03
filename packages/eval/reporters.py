"""Report writers for machine and human eval artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from packages.eval.runner import EvalRunResult


def write_reports(run_result: EvalRunResult, output_dir: Path) -> tuple[Path, Path]:
    """Write JSON and Markdown reports for a completed eval run."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "evaluation-report.json"
    markdown_path = output_dir / "evaluation-report.md"

    json_path.write_text(
        json.dumps(run_result.to_report_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(run_result), encoding="utf-8")

    return json_path, markdown_path


def render_markdown(run_result: EvalRunResult) -> str:
    report = run_result.to_report_dict()
    lines = [
        "# Baseline Evaluation Report",
        "",
        f"- Evaluated at: `{report['evaluated_at']}`",
        f"- Total: {report['summary']['passed']}/{report['summary']['total']} passed",
        f"- Gate failed: `{str(report['gate_failed']).lower()}`",
        "",
        "## Results by Type",
        "",
    ]

    for eval_type, summary in sorted(report["summary"]["by_type"].items()):
        lines.append(f"- `{eval_type}`: {summary['passed']}/{summary['total']} passed")

    lines.extend(["", "## Failures", ""])
    if report["failures"]:
        for failure in report["failures"]:
            lines.append(
                "- "
                f"`{failure['eval_type']}` `{failure['suite_name']}` "
                f"on `{failure['scenario_name']}`: {failure['failure_reason']}"
            )
    else:
        lines.append("No failures.")

    lines.extend(["", "## Suite Results", ""])
    for result in report["results"]:
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(
            "- "
            f"{status} `{result['eval_type']}` `{result['suite_name']}` "
            f"fixture `{result['input_fixture']}`"
        )

    return "\n".join(lines) + "\n"
