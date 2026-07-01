# P0-05: Safety policy & privacy data-flow design

**Phase:** 0 — Feasibility & foundations | **Depends on:** none | **Parallelizable with:** all of P0 | **Surface:** docs (executable spec)

## Context (self-contained)
Baseline is **wellness decision-support, not medical advice**. The safety boundary and privacy model are product-defining and must be written down *before* the reasoning/LLM slices, because those slices consume this spec as machine-checkable rules. This slice produces specs + a machine-readable policy, not runtime enforcement (that is P3-05).

## Goal
Author the safety policy, the confidence/uncertainty policy, the data-classification + privacy data-flow model, and a threat model — with the safety rules encoded in a structured, testable form the safety engine and evals will load.

## Scope
In:
- `docs/safety/policy.md`: allowed vs refused/redirected behaviors (§19.6) — refuse diagnosis, treatment, medication/supplement dosing, injury rehab protocols, sexual-dysfunction dx/tx, "trend proves a condition" claims, emergency triage beyond "seek help"; allowed: wellness interpretation, load/recovery tradeoffs, general options, cited explanations, "ask a clinician" prompts.
- `docs/safety/confidence-policy.md`: when to reduce confidence and when to prefer conservative recommendations (§19.7).
- **Machine-readable policy** (`packages/eval/policy/safety_policy.yaml` or `.json`): categories, trigger patterns, required disclaimers, escalation strings — the schema P3-05 and safety evals will consume.
- `docs/privacy/data-flow.md`: data classification (§20.2), what may leave the device, LLM data-minimization rules (§20.4), retention defaults (§20.5), and a threat model of sensitive data flows.
- App Store / SaMD / HIPAA / FTC positioning notes (§19.2–19.5) as guardrail checklist.

Out:
- Runtime safety enforcement, redaction code (P0-06 / P3-05).

## Deliverables
- The docs above + one versioned machine-readable safety policy file with a documented schema.

## Acceptance criteria
- [ ] Every refusal category in §19.6 appears in the machine-readable policy with at least one trigger and a redirect/escalation string.
- [ ] Confidence-reduction and conservative-recommendation triggers (§19.7) enumerated and mapped to feature signals.
- [ ] Data-flow doc classifies every §15 entity and states its external-LLM exposure rule.
- [ ] Policy file has a `policy_version`.

## Tests required
- Schema-validation test for the machine-readable policy (it loads and conforms).
- A checklist test asserting all §19.6 categories are represented.

## PRD references
§19 Safety/Compliance, §20 Privacy/Security, §21.4 prompting requirements, §26.16–17.
