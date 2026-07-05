# Privacy

Baseline handles restricted health and lifestyle data. The privacy posture is
local-first, consent-gated, and minimization-oriented: use the smallest
structured personal context needed for the current task, keep raw records out of
model prompts and logs, and make deletion/export/audit behavior explicit.

## Data Classes

| Class | Examples | Default handling |
| --- | --- | --- |
| Restricted | Raw HealthKit samples, manual check-ins, free-text note references, consent records, personal recommendations. | Keep internal; do not send to external LLMs by default; redact from logs and docs. |
| Confidential | Derived features, memory summaries, readiness assessments, model-run metadata. | May be used internally and minimized for model context when consent allows. |
| Internal | Synthetic fixtures, public architecture docs, non-identifying operational aggregates. | Safe for portfolio/demo use when leak checks pass. |

The detailed entity-by-entity classification is in [data-flow.md](data-flow.md).

## Privacy Controls In Code

| Control | Location | Notes |
| --- | --- | --- |
| Runtime settings | `baseline_api.config.Settings` | Configuration is environment-only; no checked-in secrets. |
| Consent | `baseline_api.privacy.consent` and `/v1/data/consent*` | Consent records gate processing choices and support revocation history. |
| Export | `baseline_api.privacy.export` and `POST /v1/data/export` | Produces scoped user data packages. |
| Deletion | `baseline_api.privacy.delete` and `/v1/data/*` delete routes | Supports account, check-in, note, and memory-summary deletion paths. |
| Audit events | `baseline_api.privacy.audit` | Records privacy actions with redacted metadata. |
| Model disclosures | `baseline_api.privacy.disclosure` and `/v1/data/model-disclosures` | Exposes model/provider disclosure information without raw prompt payloads. |
| Redaction | `baseline_api.observability.redaction` | Default-deny metadata redaction for logs and traces. |
| Demo leak checks | `packages.eval.demo` | Scans generated demo artifacts for private-data markers. |

## External Model Boundary

External LLM processing is not the source of truth. When enabled, it receives
minimized structured context such as derived features, readiness assessment
fields, selected goal tradeoffs, safe memory summaries, and public citations.

It must not receive:

- raw HealthKit samples,
- arbitrary row-level metric dumps,
- raw free-text notes,
- secrets or credentials,
- raw prompt payloads stored for later replay,
- diagnosis, treatment, or dosing instructions as acceptable output.

Model-run records store hashes, model/provider metadata, schema versions, token
usage, cost, latency, and safety results. They do not store raw prompts by
default.

## Portfolio Demo Privacy

Public demo and eval artifacts are generated from deterministic synthetic
fixtures. The demo path must not require Apple Health exports, real names,
contact details, production tokens, private anecdotes, or real health records.
The current demo leak suites cover briefing, trace, memory, dashboard, and
export payloads.

## Reviewer Checklist

- Does a user-facing or demo artifact contain only synthetic or public data?
- Does any new model path pass through consent, minimization, schema validation,
  model-run logging, and safety validation?
- Are personal evidence and external citations still separate?
- Does deletion/export touch every table introduced by the feature?
- Can operational logs explain failures without exposing raw samples, notes,
  prompts, or secrets?
