# Sync Failures

Trigger: sync or backfill failures reach `SYNC_FAILURE_ALERT_THRESHOLD`.

Initial checks:
- Inspect recent `BackfillJob.last_error` and import-batch quality summaries.
- Verify client anchors, HealthKit permissions, and source-device clock/timezone settings.
- Confirm served briefings clearly flag stale or unavailable sync freshness.

Stop condition: sync resumes and stale-source flags clear on the next successful import.
