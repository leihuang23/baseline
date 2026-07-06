# Privacy Data Flow

Baseline handles health and lifestyle data under a local-first, data-minimizing
model. Restricted data should remain on device or in controlled first-party
storage unless the user explicitly enables a narrower external flow. Raw health
data, free-text notes, and raw LLM prompts are not external-LLM inputs by
default.

This document covers PRD 20.2 data classification, PRD 20.4 LLM controls, PRD
20.5 retention defaults, and the sensitive data-flow threat model.

## Classification Rules

| Classification | Meaning | Examples |
|----------------|---------|----------|
| Restricted | Identifiable health, subjective, consent, note, prompt, or personalized interpretation data. Highest protection. | Raw HealthKit samples, manual check-ins, free-text notes, sexual-health notes, raw personal prompts, personal recommendations. |
| Confidential | Derived or compressed personal data that still reveals health, behavior, goals, or model traces. | Derived features, memory summaries, readiness assessments, model-run metadata. |
| Internal | Non-identifying operational, synthetic, or public documentation data. | Synthetic demo data, public architecture docs, aggregated non-identifying metrics. |

## Entity Classification And External LLM Exposure

| PRD 15 entity | Classification | External LLM exposure rule |
|---------------|----------------|----------------------------|
| User | Restricted | Do not send identifiers, locale, timezone, privacy mode, or consent fields to an external LLM. Use only non-identifying runtime controls, such as a coarse privacy mode flag, when required. |
| Consent Record | Restricted | Do not send. Consent gates whether an external call is allowed but the record itself stays internal. |
| Raw Health Sample | Restricted | Do not send raw samples. Use derived daily features when personal evidence is needed. |
| Normalized Health Metric | Confidential | Do not send row-level metrics by default. Send only minimized aggregate values when derived features are insufficient and consent allows external processing. |
| Workout Session | Confidential | Do not send raw session rows by default. Send minimized summaries such as modality bucket, duration range, load trend, and recency only when needed. |
| Sleep Session | Confidential | Do not send raw sleep sessions by default. Send minimized derived sleep features such as sleep debt, duration bucket, and data-quality flags when needed. |
| Daily Check-In | Restricted | Do not send raw check-in records or free-text references. Send only selected structured scores/flags needed for the task after local redaction and consent checks. |
| Goal | Confidential | Send only goal category, priority, and relevant constraints needed to explain tradeoffs. Do not send unnecessary history or identifiers. |
| Derived Daily Feature | Confidential | Preferred personal-data input for LLMs when external processing is enabled. Include only task-relevant features, data-quality flags, and evidence references. |
| Readiness Assessment | Confidential | May be sent as structured assessment context only after minimization and consent checks. Include confidence, uncertainty, risk flags, and evidence summaries; exclude identifiers. |
| Recommendation | Restricted | Do not send accepted actions, feedback, or full personalized recommendation history unless required for a user-requested explanation and consent allows it. Prefer local retrieval and summarized context. |
| Memory Summary | Confidential | May be sent only as a minimized, source-linked summary with `sensitive_fields_excluded` honored. Do not send raw source records behind the summary. |
| Knowledge Source | Internal | May be sent or cited when license permits. It must remain separated from personal evidence. |
| Model Run | Confidential | Do not send raw prompt/output payloads. Store and expose hashes, provider metadata, schema versions, token usage, cost, latency, and safety result only. |
| Evaluation Case | Confidential when derived from real data; Internal when synthetic | Do not send real-data eval cases externally. Synthetic eval cases may be sent if they contain no private data and are marked synthetic. |
| Audit Event | Restricted before redaction; Internal only for redacted or aggregated derivatives | Do not send source audit records or arbitrary metadata externally. Unredacted audit events include identifiers and may describe sensitive health/privacy actions, so they require Restricted handling; only redacted operational aggregates may be used for internal monitoring, not LLM prompting. |

## Sensitive Data Flows

| Flow | Data | Boundary | Controls |
|------|------|----------|----------|
| HealthKit to iOS cache | Raw health samples | Device-local restricted store | HealthKit permission scoping, platform data protection, local encryption. |
| iOS to API sync | Raw and normalized health data | Device to first-party backend | TLS, consent checks, idempotency keys, no health data in logs. |
| Manual check-in | Scores, flags, structured notes, optional free-text reference | Device to first-party backend | Explicit user entry, sensitive-note policy, local redaction where possible. |
| Feature computation | Raw/normalized/check-in data to derived features | First-party backend worker | Deterministic computation, versioned features, no external LLM call. |
| Reasoning | Derived features, goals, memories, evidence refs to readiness assessment | First-party backend worker | Use structured records, maintain confidence and uncertainty, no medical claims. |
| External LLM explanation or Q&A | Minimized structured features, assessment, safe memory summary, retrieved public knowledge | First-party backend to external provider | Requires external LLM consent, payload minimization, no raw samples, no raw notes, prompt/output hashes only. |
| Safety evaluation | Generated output plus policy category checks | First-party backend/eval harness | Hard gate after generation; record safety status and redacted result. |
| Export | User-selected data package | First-party backend to user | Encrypted export file, expiring link, explicit scope; decryption key returned once in the create response and not retained server-side. |
| Observability | Operational metadata | Service to logs/metrics | Redaction, short retention, no secrets, no health data, no raw prompts. |

## LLM Data-Minimization Rules

External LLM payloads must:

- Be disabled unless user consent permits external LLM processing.
- Include the product safety boundary and schema instructions.
- Use derived features, assessments, and source references instead of raw samples.
- Exclude raw free-text notes and sexual-health related notes.
- Include only retrieved evidence needed for the current answer.
- Separate personal evidence from external knowledge citations.
- Require explicit uncertainty and prohibit diagnosis, treatment, dosing, and
  fabricated metrics.
- Persist only hashes and structured trace metadata by default.
- Support "view data sent" from metadata and reconstruction of minimized payload
  shape without storing raw prompt text.

## Retention Defaults

| Data | Default retention |
|------|-------------------|
| Raw health data | Retain while the account is active unless the user chooses shorter retention. |
| Derived features | Retain while the account is active. |
| Free-text notes | User-configurable retention; prefer redacted references over raw text. |
| LLM prompt payloads | Do not persist raw prompts by default; persist hashes and structured trace metadata. |
| Logs | Short retention with redaction. |
| Exports | Encrypted files with expiring links. |

Deletion and export controls are implemented in later slices. Until then, docs,
tests, and fixtures must not contain real secrets, raw health data, free-text
notes, or prompt payloads.

## Threat Model

| Threat | Sensitive flow | Risk | Required mitigation |
|--------|----------------|------|---------------------|
| Raw health data leaks into logs | Sync, feature computation, observability | Restricted health data disclosure | Redaction-by-default logging, tests for no raw health data in logs, structured metadata only. |
| External LLM receives raw samples or notes | Explanation, Q&A, memory generation | Unnecessary third-party disclosure | Consent gate, minimization, local note redaction, deny raw sample and raw note fields. |
| Prompt payload retention exposes personal health context | Model-run logging | Stored prompt breach | Persist hashes and schema metadata, not raw payloads. |
| Personal and external knowledge are mixed | Retrieval and explanation | Unsupported personalization or fabricated evidence | Keep personal evidence and external citations in separate fields with source refs. |
| Synthetic portfolio data contaminated with real data | Eval/demo export | Private data leak in public artifacts | Mark synthetic fixtures, keep real-data evals confidential, add demo leak tests in later slices. |
| Consent revocation not honored | Any external processing | Processing after user opt-out | Consent record gates jobs; revoked consent must stop new external calls. |
| Export link exposure | Data export | Full account disclosure | Encrypted export file, expiring link, explicit scope. |
| Audit metadata re-identifies user | Audit trail and metrics | Indirect privacy leak | Redact event metadata and aggregate before internal reporting. |
