# Schema Validation Failures

Trigger: structured LLM output validation failures reach `SCHEMA_VALIDATION_FAILURE_ALERT_THRESHOLD`.

Initial checks:
- Inspect prompt version, schema version, model name, and validation failure counts.
- Compare failures against recent prompt or schema changes.
- Prefer deterministic briefing output while malformed outputs continue.

Stop condition: schema-valid output rate returns to the expected threshold.
