# Deployment Readiness

Baseline's checked-in infrastructure is for local development and portfolio
review. A deployable environment must provide the following controls before the
API is reachable from any untrusted network.

## Required Runtime Controls

- Set `APP_ENV=production`.
- Set `BASELINE_API_AUTH_TOKEN` to a high-entropy secret. For a private
  single-user iOS build, the client can send the same value through
  `BASELINE_API_AUTH_TOKEN` or `BaselineAPIAuthToken`, but an embedded mobile
  token is extractable and must not be treated as account-level authentication.
- Terminate TLS at the ingress or platform load balancer. Do not expose the API
  over plaintext HTTP outside local development.
- Run both processes:
  - `uv run uvicorn baseline_api.main:app`
  - `python -m arq baseline_api.worker.WorkerSettings`
- Use managed or hardened Postgres and Redis. Do not use the local
  `POSTGRES_HOST_AUTH_METHOD=trust` compose service for production data.
- Set `EXPORT_STORAGE_DIR` to a durable private directory owned by the API
  process. Production and staging reject temp-backed export storage.
- Set `EXPORT_RETENTION_HOURS` and leave `EXPORT_CLEANUP_ON_START=true` unless
  an external lifecycle job owns encrypted export cleanup.

## Data Operations

- Enable automated Postgres backups and test restore before storing real health
  data.
- Restrict Redis and Postgres network access to the API and worker.
- If the iOS app uses the shared API token, restrict API ingress to trusted
  networks/devices; do not expose the API to the public internet with only that
  embedded token.
- Keep `.env` and provider keys out of source control and logs.
- Export download keys are returned only in the create response. Do not persist
  response bodies that contain `encryption.key_base64` in logs or dashboards.
- Monitor `/health`, `/v1/health/ping`, sync failures, briefing failures, model
  provider failures, schema validation failures, deletion failures, and cost
  budget alerts.

## First-User Bootstrap

The iOS onboarding flow records consent with `/v1/data/consent`. On an empty
single-user deployment, the first consent request atomically creates the
Baseline `User` and an active `ConsentRecord`, and returns the active consent
version. The iOS client persists that server-returned version before the first
HealthKit sync, and subsequent sync requests must use the current active version.
If more than one user exists, single-user privacy controls (consent and health
sync) fail closed with `409 ambiguous_user` until an authenticated multi-user
resolver is implemented.

## Known Non-Goals

This hardening is sufficient for a private single-user deployment or portfolio
review environment only when ingress is also restricted. It is not a
multi-tenant identity system. Before closed beta, replace the shared API token
with account-level authentication and authorization.
