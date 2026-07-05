# Safety

Baseline is wellness decision support for training and recovery. It is not a
medical device, clinical decision-support system, diagnosis tool, treatment
planner, supplement or medication dosing advisor, or emergency triage service.

The safety design has two layers:

1. Deterministic reasoning produces readiness, risk flags, uncertainty,
   candidate options, and hard safety flags before generation.
2. The safety engine validates generated text after the LLM and can pass,
   block, rewrite, or escalate the response.

## Product Boundary

Baseline may:

- explain wellness, training, recovery, sleep, and goal-tradeoff signals,
- compare non-clinical options such as rest, easy movement, mobility, zone 2,
  technique work, or reduced intensity,
- show evidence, confidence, uncertainty, and trace details,
- suggest clinician-facing questions when a concern is medical-adjacent.

Baseline must not:

- diagnose or rule out a disease, injury, deficiency, disorder, or infection,
- prescribe treatment, medication, supplement dosing, or rehab protocols,
- tell a user to train through pain or symptoms,
- claim a trend proves a medical condition,
- use private raw health data or raw notes as public demo material.

The detailed source policy is [policy.md](policy.md). Confidence and
uncertainty rules are in [confidence-policy.md](confidence-policy.md).

## Runtime Enforcement

| Layer | Location | Responsibility |
| --- | --- | --- |
| Reasoning hard flags | `baseline_api.reasoning.engine` | Caps recommendation intensity for illness, missing/stale data, conflicting signals, sleep debt, high density, and other deterministic risk flags. |
| Generated-output gate | `baseline_api.safety.engine` | Applies the versioned machine-readable policy from `packages/eval/policy/safety_policy.json`. |
| Briefing assembly | `baseline_api.briefing.service` | Runs safety validation before persisting or returning user-facing recommendations. |
| Evals | `packages/eval/safety_scenarios.py` | Exercises blocked, rewritten, and escalated outcomes for adversarial generated text. |

Safety validation is a hard gate. If generated text violates the policy, the
system records a safety result and returns safe boundary language rather than
the unsafe text.

## User-Facing Recommendation Contract

Every recommendation should expose:

- personal evidence,
- optional external citations,
- confidence,
- uncertainty,
- safety status,
- candidate alternatives or follow-up prompts when confidence is limited,
- trace metadata that lets a reviewer inspect the pipeline.

This is why the product is framed as decision support instead of advice
authority. The system should help the user understand tradeoffs, not tell them
what a medical state is or what treatment to follow.

## Failure Modes

Known degraded behavior and operator response are documented in
[failure-modes.md](failure-modes.md) and the runbooks under `docs/runbooks/`.
