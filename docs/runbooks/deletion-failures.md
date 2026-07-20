# Deletion Failures

Trigger: deletion failure audit events reach `DELETION_FAILURE_ALERT_THRESHOLD`.

Initial checks:
- Inspect redacted audit metadata for target and error code.
- Retry only after confirming the failed target and storage/export backend state.
- Do not expose deleted-user identifiers; use stored hashes or internal UUIDs only in secure operator context.

Stop condition: deletion completes, audit trail records success, and no orphaned user-owned rows remain.
