# Evaluation Harness

Baseline uses a scenario-driven evaluation harness as part of the MVP foundation. It is
offline by default, uses only synthetic fixtures, and writes both database rows and report
artifacts for later dashboard ingestion.

## Shape

Each suite declares:

- `name`: stable suite identifier used by the registry and CLI.
- `eval_type`: one of `deterministic`, `llm_property`, `retrieval`, `safety`, `privacy`,
  or `regression`.
- `scenario_name`: scenario label persisted to `EvaluationCase`.
- `input_fixture`: named synthetic fixture from `packages.fixtures`.
- `expected_properties`: structured properties the scorer checks.
- `scorer`: deterministic code that returns pass/fail, observed properties, and an optional
  failure reason.

LLM-property suites must provide a mocked or recorded response. The harness has no live model
client and `make eval` is safe for CI.

## Persistence and Reports

Every suite run writes an `EvaluationCase` row:

- `input_fixture` stores the synthetic fixture payload.
- `expected_properties` stores the suite expectations.
- `actual_output` stores `suite_name`, `eval_type`, observed properties, optional mocked model
  response, and optional failure reason.
- `pass_fail`, `failure_reason`, and `evaluated_at` store the final score.

The reporter writes:

- `artifacts/eval/evaluation-report.json`
- `artifacts/eval/evaluation-report.md`

The JSON report includes summary totals, totals by eval type, gated failure types, failures,
and per-suite result records with `EvaluationCase` IDs.

## CI Gate

`make eval` runs the default registry through `python -m packages.eval`.
CI runs `make migrate` before the eval gate so the service database has the
`EvaluationCase` table before results are persisted.

The gate returns nonzero when any `safety` or `regression` suite fails. Other eval failures are
reported but do not fail the P0 gate unless they are promoted to one of those gated types in a
later task.
