# Cost Budget Exceeded

Trigger: daily briefing model cost exceeds `DAILY_BRIEFING_COST_BUDGET`.

Initial checks:
- Inspect recent `ModelRun` aggregates by model and feature.
- Confirm whether fallback or schema-repair retries increased run count.
- Lower traffic, disable external LLM consent for nonessential runs, or route to the cheaper model until costs return under budget.

Stop condition: cost for the current alert window is below budget and the cause is documented.
