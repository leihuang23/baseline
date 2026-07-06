# Stale Daily Briefing

Trigger: no `DailyAnalysisJob` for the current UTC day has reached `completed` status with a populated `recommendation_id` by `STALE_BRIEFING_ALERT_HOUR_UTC`.

Initial checks:
- Confirm the current UTC time and the configured `STALE_BRIEFING_ALERT_HOUR_UTC`.
- Check the worker queue / cron logs for the `daily_briefing_cron` job.
- Look for failed or stuck `DailyAnalysisJob` rows for today's date.
- Verify Redis and Postgres connectivity from the worker.

Stop condition: a daily briefing job for the current UTC day completes with a recommendation, or the alert is acknowledged as an expected outage.
