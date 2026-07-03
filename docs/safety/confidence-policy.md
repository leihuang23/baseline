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
| Data is missing or stale | `derived_daily_feature.data_quality.missing_sources`, `data_quality.stale_sources`, `data_freshness.stale_sources`, missing `latest_sample_at` | Reduce confidence at least one level and name the missing or stale source. |
| Sleep data is incomplete | `sleep_features.duration_missing`, `sleep_features.stage_coverage_pct` below threshold, absent `sleep_session` for target night | Reduce confidence and avoid sleep-specific certainty. |
| HRV/RHR baselines are not established | `hrv_features.baseline_days` below minimum, `rhr_features.baseline_days` below minimum, `data_quality.baseline_status` is `insufficient` | Reduce confidence and describe the result as early or provisional. |
| Manual check-in is absent | Missing `daily_check_in`, absent `perceived_recovery_score`, absent soreness/energy/stress scores | Reduce confidence and ask for subjective context when useful. |
| Recent illness, injury, or travel flags exist | `daily_check_in.illness_flag`, `injury_flag`, `travel_flag`; `derived_daily_feature.recovery_features.recent_disruption_flags` | Reduce confidence because physiological signals may not reflect normal baseline. |
| Conflicting indicators exist | Favorable `hrv_features` with unfavorable `sleep_features`, elevated `rhr_features` with good subjective recovery, `anomaly_flags` containing `conflicting_signals` | Reduce confidence and present competing interpretations. |
| Recommendation depends on external research not specific to the user | `include_external_knowledge` true with weak personal evidence, `knowledge_source` cited without matching personal trend, `evidence_items.source` is `external_knowledge` only | Reduce confidence and separate general evidence from personal evidence. |

## Conservative-Recommendation Triggers

| Trigger | Feature signals | Conservative posture |
|---------|-----------------|----------------------|
| High injury or illness flags exist | `daily_check_in.injury_flag`, `illness_flag`, `risk_flags` containing `injury` or `illness` | Prefer rest, reduced load, or professional consultation language. |
| Resting HR is significantly elevated | `rhr_features.delta_from_baseline_pct`, `rhr_features.z_score`, `risk_flags` containing `elevated_rhr` | Avoid high-intensity recommendations unless clearly justified. |
| Sleep debt is high | `sleep_features.sleep_debt_hours`, `sleep_features.sleep_deficit_rolling`, `risk_flags` containing `high_sleep_debt` | Prefer lower intensity, recovery, or technique work. |
| Training density is high | `training_load_features.sessions_rolling_7d`, `training_load_features.load_density`, `risk_flags` containing `high_training_density` | Avoid adding another high-load session; suggest lower-load alternatives. |
| User reports high soreness or poor recovery | `daily_check_in.soreness_score` high, `perceived_recovery_score` low, `energy_score` low | Prefer reduced load and ask for context if needed. |
| Model output fails safety validation | Safety verdict is `blocked`, `rewritten`, or `needs_human_review`; `recommendation.safety_status` is not `passed` | Block or rewrite the output and choose the safest allowed alternative. |

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
