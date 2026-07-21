# Evaluation Report

Baseline uses an offline, synthetic-data evaluation harness for deterministic
features, reasoning behavior, external retrieval, safety policy, privacy leak
checks, LLM-output properties, and regression coverage. The default registry is
defined in `packages/eval/suites.py` and runs through:

```bash
make eval
```

The harness persists `EvaluationCase` rows and writes:

- `artifacts/eval/evaluation-report.json`
- `artifacts/eval/evaluation-report.md`

## Current Suite Inventory

The default registry currently contains 58 suites:

| Eval type | Count | What it covers |
| --- | ---: | --- |
| `reasoning` | 31 | Golden and variant scenario checks for readiness state, recommendation band ceilings, risk flags, evidence, confidence, uncertainty, trace IDs, goal tradeoffs, and safety routing. |
| `safety` | 14 | Diagnosis, treatment, dosing, emergency, injury-rehab, sexual-health, and trend-proves-condition refusal/rewrite/escalation behavior. |
| `privacy` | 6 | Demo-mode artifact leak checks across selectable public scenarios. |
| `retrieval` | 4 | Curated external knowledge relevance, citation binding, separation from personal evidence, disabled-external-knowledge handling, and unsupported medical-claim suppression. |
| `regression` | 1 | Feature-engine golden bundle regression. |
| `deterministic` | 1 | Fixture expected-outcome smoke coverage. |
| `llm_property` | 1 | Mocked model response property check for medical-boundary behavior. |

`reasoning`, `safety`, `privacy`, and `regression` failures are CI-gated by
`packages.eval.runner.GATED_FAILURE_TYPES`. Other suite types are reported and
can be promoted later if their risk changes.

## Golden Scenario Coverage

The fixture catalog in `packages/fixtures/scenarios.py` includes the 11 named
golden scenarios:

- `high_hrv_good_sleep_low_load`
- `low_hrv_high_rhr_poor_sleep`
- `mixed_high_hrv_sleep_debt`
- `three_lower_body_sessions_six_days`
- `illness_flag_high_motivation`
- `missing_hrv`
- `stale_sleep`
- `vo2_improving_recovery_declining`
- `cognitive_priority_week`
- `missing_strength_data`
- `medical_diagnosis_request`

It also includes 20 synthetic variants across sleep, training, and recovery
families, plus `demo_60_day_persona`. The reasoning eval therefore covers 31
golden/variant readiness scenarios, satisfying the portfolio requirement for at
least 30 golden-style scenarios without using private data.

## What The Harness Proves

### Deterministic And Regression

The deterministic and regression suites verify that fixture expectations and the
feature-engine golden bundle stay stable. These suites are intentionally based
on structured feature objects, not generated prose.

### Reasoning

Reasoning suites call `baseline_api.features.assembler.assemble_daily_features`
and `baseline_api.reasoning.engine.assess_readiness`. They assert structural
properties such as:

- evidence must be present,
- confidence must be present,
- uncertainty must be present,
- trace IDs and input hashes must round-trip,
- safety and data-quality flags must cap recommendation intensity,
- missing or stale inputs must reduce confidence and be disclosed.

### Retrieval

The retrieval suites run the starter external corpus through
`packages.knowledge.pipeline.KnowledgeIngestionPipeline`, retrieve relevant
chunks, and check `baseline_api.retrieval.bind_external_claims`. They verify
that external citations are relevant, are not mixed into personal evidence,
are skipped when external knowledge is disabled, and that unsupported medical
claims are suppressed without citations.

### Safety

The safety suites evaluate `baseline_api.safety.engine.SafetyPolicyEngine`
against adversarial requests and generated text. They check blocked, rewritten,
and escalated outcomes for the refusal categories documented in
`docs/safety/policy.md`.

### Privacy And Demo

The privacy suites build deterministic demo artifacts through
`packages.eval.demo` and scan briefing, trace, memory, dashboard, and export
payloads for private-data markers. They also verify that demo mode exercises
product loaders and persistence instead of bypassing the real pipeline.

### LLM Property

The LLM property suite uses a recorded/mock response. The default eval gate does
not call a live model provider, which keeps CI deterministic and safe.

## Current Results

Latest local run:

- Command: `make eval`
- Evaluated at: `2026-07-06T01:48:04.572260+00:00`
- Total pass rate: 58/58 suites passed (100%)
- Gate failed: `false`
- Failure count: 0

| Eval type | Current result |
| --- | ---: |
| `deterministic` | 1/1 passed |
| `llm_property` | 1/1 passed |
| `privacy` | 6/6 passed |
| `reasoning` | 31/31 passed |
| `regression` | 1/1 passed |
| `retrieval` | 4/4 passed |
| `safety` | 14/14 passed |

The run wrote the detailed suite list to `artifacts/eval/evaluation-report.md`
and `artifacts/eval/evaluation-report.json`. Those generated artifacts are not
checked into source control, so rerun `make eval` after changing feature,
reasoning, retrieval, safety, privacy, demo, or eval code.

## Known Limits

- The harness proves behavior over synthetic scenarios only.
- It does not claim clinical validation or population-level accuracy.
- The LLM-property suite is mocked; live provider quality belongs in later
  recorded or shadow evals.
- Database-backed eval persistence requires a reachable configured database.
