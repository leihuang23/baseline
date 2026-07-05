# Demo Walkthrough

This walkthrough is for a hiring manager or technical reviewer. It shows the
Baseline loop with deterministic synthetic data only: sync, feature generation,
reasoning, LLM explanation/fallback metadata, safety validation, memory, trace
inspection, and dashboard presentation.

## Preconditions

- No Apple Health export is required.
- No production secrets are required.
- No live model provider is required for the offline demo path.
- Demo artifacts are generated from `packages/fixtures` and checked by privacy
  leak suites in `packages/eval.demo`.

## Generate Demo Artifacts

```bash
make demo
```

The command writes deterministic artifacts through `packages.eval.demo`. The
default scenario is `demo_60_day_persona`, a 60-day synthetic persona with
travel, sleep debt, illness, and an improving fitness trend.

The demo scenario catalog also includes:

- `low_hrv_high_rhr_poor_sleep`
- `mixed_high_hrv_sleep_debt`
- `three_lower_body_sessions_six_days`
- `illness_flag_high_motivation`
- `missing_hrv`

## Review The Pipeline Story

Read the generated manifest and trace in order:

1. Ingestion: synthetic HealthKit-like payload and structured check-in.
2. Features: versioned derived daily feature fields.
3. Reasoning: readiness state, recommendation band, risk flags, evidence,
   confidence, uncertainty, candidate options, and trace ID.
4. Retrieval: personal evidence and optional external knowledge stay separate.
5. LLM explanation: recorded/mock provider metadata for deterministic demo mode.
6. Safety: safety verdict applied before user-facing output is accepted.
7. Memory: daily and weekly summaries with source references.
8. Dashboard/export: sanitized public demo payloads.

## Dashboard Review

The dashboard can be opened directly in demo mode:

```bash
npm test --prefix apps/dashboard
```

`apps/dashboard/index.html` renders static demo data when opened without a host
operator context. Real operator mode requires the host page to provide
`window.BASELINE_DASHBOARD_AUTH` and sanitized `window.BASELINE_DASHBOARD_DATA`;
otherwise it shows only the authentication gate.

## What To Look For

- The briefing contains evidence, confidence, uncertainty, and safety status.
- Missing/stale data is disclosed instead of invented.
- Illness or safety flags cap training recommendations.
- External citations, when present, are separate from personal evidence.
- The trace shows feature, reasoning, retrieval, model, and safety metadata.
- Demo payloads contain no names, contact details, real HealthKit exports,
  secrets, raw notes, or raw prompt payloads.

## Verification

Run the focused checks:

```bash
make docs-check
env UV_CACHE_DIR=.uv-cache uv run pytest apps/api/tests/test_demo_mode.py apps/api/tests/test_portfolio_docs.py
```

For the full project gate:

```bash
make lint
make typecheck
make test
make eval
npm test --prefix apps/dashboard
swift test --package-path apps/ios
```
