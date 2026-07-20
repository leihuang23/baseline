# Daily Briefing Generation Failures

Trigger: failed daily briefing jobs reach `DAILY_BRIEFING_FAILURE_ALERT_THRESHOLD`.

Initial checks:
- Review failed `DailyAnalysisJob.error_code` values and stage traces.
- Confirm deterministic degraded mode is still serving the latest usable briefing.
- Check feature computation, retrieval, safety, and persistence dependencies in that order.

Stop condition: new daily briefing jobs complete or intentionally degrade without failing.
