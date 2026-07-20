# Confidence And Uncertainty Policy

Baseline confidence describes how much the system should trust a wellness
recommendation given data completeness, signal quality, user context, and safety
validation. Confidence is not medical certainty.

The reasoning engine must expose uncertainty rather than hide it. When a trigger
below is present, the recommendation should reduce confidence, add an uncertainty
note, or both. When a conservative trigger is present, the system should prefer
lower-risk training options unless strong personal evidence supports otherwise.

## Confidence Levels

| Level | Meaning | Output posture |
|-------|---------|----------------|
| `high` | Recent, complete, non-conflicting personal data supports the recommendation. | Explain the evidence and still name material uncertainty. |
| `medium` | Data is usable but incomplete, stale, weakly personalized, or partly conflicting. | Prefer options and tradeoffs; avoid strong language. |
| `low` | Data is missing, stale, anomalous, high risk, or mostly external/general. | Use cautious framing, ask for missing inputs, and prefer low-risk options. |

## Confidence-Reduction Triggers

| Trigger | Feature signals | Required effect |
|---------|-----------------|-----------------|
| Data is missing or stale | Section `data_quality.completeness` below 1.0, `data_quality.flags` entries with `missing_` / `stale_` prefixes (e.g. `missing_sleep`, `stale_sleep`), `data_freshness.stale_sources`, missing `data_freshness.latest_sample_at` | Reduce confidence at least one level and name the missing or stale source. |
| Sleep data is incomplete | `sleep_features.status` is `insufficient_data`, `sleep_features.values.duration_hours` missing, `data_quality.flags` containing `missing_sleep`, absent `sleep_session` for target night | Reduce confidence and avoid sleep-specific certainty. |
| HRV/RHR baselines are not established | `hrv_features.values.baseline_ms` or `rhr_features.values.baseline_bpm` has status `baseline_not_established` (28-day rolling window, minimum 7 days) | Reduce confidence and describe the result as early or provisional. |
| Manual check-in is absent | Missing `daily_check_in`, absent `perceived_recovery_score`, absent soreness/energy/stress scores | Reduce confidence and ask for subjective context when useful. |
| Recent illness, injury, or travel flags exist | `daily_check_in.illness_flag`, `injury_flag`, `travel_flag`; reasoning `hard_safety_flags` containing `illness` or `injury` | Reduce confidence because physiological signals may not reflect normal baseline. |
| Conflicting indicators exist | `favorable_hrv` with `high_sleep_debt`, good `perceived_recovery_score` with `elevated_rhr`, `risk_flags` containing `conflicting_signals` | Reduce confidence and present competing interpretations. |
| Recommendation depends on external research not specific to the user | `include_external_knowledge` true with weak personal evidence (rule `external_knowledge_requested`), evidence items lacking personal feature sources | Reduce confidence and separate general evidence from personal evidence. |

## Conservative-Recommendation Triggers

| Trigger | Feature signals | Conservative posture |
|---------|-----------------|----------------------|
| High injury or illness flags exist | `daily_check_in.injury_flag`, `illness_flag`, `risk_flags` containing `injury` or `illness` | Prefer rest, reduced load, or professional consultation language. |
| Resting HR is significantly elevated | `rhr_features.values.deviation_pct` at or above +8%, or `rhr_features.values.deviation_bpm` at or above +5 bpm, `risk_flags` containing `elevated_rhr` | Avoid high-intensity recommendations unless clearly justified. |
| Sleep debt is high | `sleep_features.values.sleep_debt_hours` at or above the 1.0h / 2.0h thresholds, `risk_flags` containing `high_sleep_debt` | Prefer lower intensity, recovery, or technique work. |
| Training density is high | `training_load_features.values.density_by_muscle_group` or `density_by_modality` (6-day window) at or above 3 sessions, `risk_flags` containing `high_training_density` | Avoid adding another high-load session; suggest lower-load alternatives. |
| User reports high soreness or poor recovery | `daily_check_in.soreness_score` high, `perceived_recovery_score` low, `energy_score` low | Prefer reduced load and ask for context if needed. |
| Model output fails safety validation | Safety verdict is `blocked`, `rewritten`, or `escalated`; `recommendation.safety_status` is not `passed` | Block or rewrite the output and choose the safest allowed alternative. |

## Output Requirements

Every user-facing recommendation should include:

- Confidence level.
- One or more evidence items when evidence exists.
- Uncertainty notes for each active confidence-reduction trigger.
- Conservative framing when any conservative trigger is active.
- A safety note when the topic is medical-adjacent or the safety policy rewrites
  the output.

The system must not use high confidence when any hard refusal category is
triggered. A refused response should report safety status rather than confidence
in the prohibited advice.
