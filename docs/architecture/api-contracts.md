# API Contracts

Baseline publishes versioned FastAPI/Pydantic contracts under `baseline_api.schemas`.
These models are the shared source of truth for iOS, dashboard, and eval harness clients.

All evolving request and response payloads include `schema_version: "v1"`. API responses use a
consistent envelope:

- `status`: `success` or `error`
- `data`: typed payload on success
- `error`: typed `code`, `message`, and optional `details`
- `meta`: envelope metadata, including envelope schema version

The P0-04 routes are intentionally behavior-free stubs. They validate request bodies, publish
OpenAPI, and return a consistent `501 not_implemented` envelope until later phase slices add
business logic.

The recommendation contract requires personal evidence, confidence, uncertainty, and safety
context for every user-facing recommendation. The PRD section 18 example validates as `v1`; when
`safety_status` is omitted but a `safety_note` is present, the contract normalizes it to `passed`
for compatibility with the published PRD example.

## Data controls

- `GET /v1/data/llm-settings` returns the operator-controlled LLM provider and model names
  (`provider`, `cheap_model`, `strong_model`, `fallback_model`). This is read-only; runtime changes
  are made via server configuration.
- `POST /v1/data/export` requests an encrypted export. The response includes a one-time
  `download_url` and AES-256-GCM encryption metadata (`key_base64`, `algorithm`, `file_sha256`).
- `GET /v1/data/export/{export_job_id}/file` downloads the encrypted export blob.
- `DELETE /v1/data/all` deletes all user data after confirmation.
- `DELETE /v1/data/checkins/{checkin_id}` and `DELETE /v1/data/checkins/{checkin_id}/note`
  delete a check-in or only its free-text note.
- `GET /v1/data/memory-summaries` lists persisted memory summaries (optional
  `period_type` filter).
- `POST /v1/data/memory-summaries/{memory_summary_id}/correct` replaces the
  observations and/or hypotheses of a memory summary (FR-067), re-aggregates
  confidence, and emits a redacted `memory_corrected` audit event. At least one
  of `observations` or `hypotheses` is required.
- `DELETE /v1/data/memory-summaries/{memory_summary_id}` deletes a memory summary
  and emits a redacted audit event.
- `POST /v1/data/consent/disable-external-llm`, `POST /v1/data/consent/revoke`,
  `GET /v1/data/consent/history`, and `GET /v1/data/model-disclosures` manage consent and
  model-run disclosures.

## Recommendation feedback

- `POST /v1/recommendations/{id}/feedback` accepts `rating`, `action_taken`,
  optional `reason`, and optional `outcome_notes`. The `DailyBriefingResponse` now includes the
  `recommendation_id` of the persisted recommendation so the iOS client can submit feedback
  directly from the briefing view.
