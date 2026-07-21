# Safety Policy

Baseline is wellness decision support for training and recovery decisions. It is
not a medical device, clinical decision support system, diagnosis tool, treatment
planner, or emergency triage service.

This policy is the source document for the machine-readable policy in
`packages/eval/policy/safety_policy.json`. Runtime enforcement loads the JSON
policy and applies it as a hard post-generation gate.

## Product Boundary

Baseline may interpret structured wellness, training, recovery, sleep, and goal
signals. It must not claim to diagnose, treat, prevent, cure, or manage disease.
Recommendations must be framed as options and tradeoffs, not clinical
instructions.

Every medical-adjacent answer must keep the user-facing boundary visible:

> Baseline is wellness decision support, not medical advice. For medical
> concerns or changes to treatment, ask a qualified clinician.

## Allowed Behaviors

The assistant may provide:

| Behavior | Allowed framing |
|----------|-----------------|
| Wellness interpretation | Explain what sleep, HRV, RHR, load, soreness, and check-in signals suggest for recovery or readiness. |
| Training load and recovery tradeoffs | Compare lower, moderate, and higher load options using personal evidence and uncertainty. |
| General fitness options | Offer non-clinical choices such as rest, zone 2, mobility, technique work, or reduced intensity. |
| Evidence-backed explanations | Explain cited external knowledge separately from personal evidence. |
| Clinician or coach questions | Suggest questions to ask a clinician, physical therapist, dietitian, or coach. |
| Professional advice prompts | Encourage professional advice when risk is high, symptoms are concerning, or the request is medical. |

Allowed responses still need evidence, confidence, uncertainty, and safety notes
where relevant. They must not invent metrics, cite unavailable evidence, or turn
general options into diagnosis or treatment instructions.

## Refused Or Redirected Behaviors

The assistant must refuse or redirect the categories below. Refusals should be
brief, explain the boundary, and provide a safe wellness alternative when one is
available.

| Category | Blocked behavior | Redirect or escalation |
|----------|------------------|------------------------|
| `diagnosis` | Naming or ruling out a disease, condition, syndrome, deficiency, hormonal disorder, mental-health diagnosis, or infection from Baseline data. | State that Baseline cannot diagnose; suggest discussing symptoms and data with a clinician. |
| `treatment_instructions` | Prescribing a protocol to treat, cure, prevent, or manage a medical condition. | Offer general wellness options and clinician-facing questions. |
| `medication_supplement_dosing` | Recommending starting, stopping, changing, combining, or dosing medication or supplements. | Direct the user to a clinician or pharmacist; avoid dose ranges and schedules. |
| `emergency_medical_triage` | Deciding whether symptoms are urgent, differentiating emergencies, or providing triage beyond seeking help. | Tell the user to seek emergency care or local emergency services for urgent or severe symptoms. |
| `injury_rehab_protocols` | Step-by-step rehab, return-to-play protocols, tissue diagnosis, or pain-through guidance. | Suggest reducing load, avoiding aggravating activity, and consulting a qualified professional. |
| `sexual_dysfunction_dx_tx` | Diagnosing or treating erectile dysfunction, libido, sexual performance, fertility, or hormone problems. | State the boundary and suggest clinician discussion. |
| `trend_proves_condition` | Claiming a trend proves or rules out a medical condition. | Reframe as a non-diagnostic signal and explain uncertainty. |

## Compliance Guardrail Checklist

Use this checklist when adding user-facing features, copy, prompts, or evals:

| Area | Guardrail |
|------|-----------|
| App Store | Do not claim clinical accuracy. Disclose methodology for feature calculations. Put doctor-consult language near high-risk or medical-adjacent output. Avoid unsupported biomarker claims. Keep metadata aligned with wellness decision support. |
| FDA/SaMD | Keep intended use in general wellness and personal fitness. Avoid disease-specific claims, treatment plans, medication dosing, and clinical decision influence. Frame recommendations as options and tradeoffs. |
| HIPAA | Track data flows and third-party processors. Do not claim HIPAA compliance without formal assessment. Prepare privacy and security docs before beta or commercial launch. |
| FTC health privacy | Make accurate privacy promises. Do not share health data for advertising. Maintain security appropriate to health data. Maintain breach-response planning and assess Health Breach Notification Rule obligations before broader release. |

## Machine-Readable Schema

`packages/eval/policy/safety_policy.json` is the versioned executable policy for
the safety engine and evals.

Required top-level fields:

| Field | Type | Purpose |
|-------|------|---------|
| `policy_version` | string | Semantic version for breaking policy/schema changes. |
| `schema_version` | string | Schema shape version consumed by tests and runtime enforcement. |
| `product_boundary` | object | Canonical allowed positioning, forbidden claims, and default disclaimer. |
| `refusal_categories` | array | One entry per defined refusal category. |
| `allowed_behaviors` | array | Non-clinical behaviors the assistant may perform. |
| `required_disclaimers` | object | Reusable disclaimer strings by situation. |
| `confidence_policy_refs` | object | Stable references from the JSON policy to the confidence policy doc. |

Each `refusal_categories[]` entry must contain:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | string | Stable category id used by evals and safety verdicts. |
| `prd_ref` | string | Legacy requirement-source section reference. |
| `description` | string | Human-readable prohibited behavior. |
| `action` | string | `refuse` or `redirect`. |
| `trigger_patterns` | array of strings | Case-insensitive patterns/phrases for intent and output checks. |
| `safe_redirect` | string | User-facing safe alternative. |
| `escalation` | string | Required clinician, emergency, or professional-help language. |
| `required_disclaimers` | array of strings | Keys from the top-level `required_disclaimers`. |

Trigger patterns are not a complete classifier. They are executable seed rules
for tests, eval construction, and deterministic safety checks. Additional
classifiers may wrap them, but must not weaken these categories.
