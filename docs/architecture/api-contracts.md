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
