# Model Provider Failures

Trigger: provider errors reach `MODEL_PROVIDER_FAILURE_ALERT_THRESHOLD`.

Initial checks:
- Confirm fallback provider/model was used and recorded in `ModelRun.safety_result`.
- Check provider status, API credentials, rate limits, and network egress.
- Keep deterministic briefing mode enabled until provider health is restored.

Stop condition: primary provider calls succeed or traffic is intentionally routed away.
