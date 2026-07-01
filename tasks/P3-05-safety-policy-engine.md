# P3-05: Safety policy engine (post-generation gate)

**Phase:** 3 — Reasoning, briefing, safety | **Depends on:** P0-05, P0-07 | **Parallelizable with:** P3-02, P3-04 | **Surface:** backend

## Context (self-contained)
Safety is a **hard gate after LLM generation** in Baseline, and it **cannot be overridden by the LLM.** It enforces the machine-readable policy authored in P0-05: refuse/redirect diagnosis, treatment, dosing, rehab protocols, sexual-dysfunction dx/tx, "trend proves a condition" claims, and emergency triage beyond "seek help". Zero unsupported medical claims in the golden eval set is an MVP metric.

## Goal
Implement the safety engine that classifies input + output risk, blocks/rewrites disallowed content, injects required disclaimers/escalation language, and records a safety result — plus an adversarial safety eval suite.

## Scope
In:
- Load the P0-05 machine-readable `safety_policy` (versioned); classify request intent + generated output against it.
- Enforce: refuse or redirect the §19.6 categories; block or rewrite high-risk output; attach doctor-consult/escalation language near medical-adjacent output (§19.2); ensure conservative framing (options/tradeoffs, not instructions).
- The engine's verdict is authoritative: it can **downgrade/replace** an LLM output and can **never be overridden** by generated content (FR-053).
- Emit a `safety_status`/`safety_result` on every recommendation + `ModelRun`.
- Adversarial **safety eval suite** (§22.2): diagnosis refusal, injury-treatment refusal, supplement-dosing refusal, sexual-dysfunction-dx refusal, emergency-symptom escalation, high-risk-output blocked/rewritten — registered in the harness + CI, target **0 unsupported medical outputs**.

Out:
- Prompt construction / generation (P3-04); briefing assembly (P3-06).

## Deliverables
- `baseline_api/safety/` engine + a registered safety eval suite in `packages/eval`.

## Acceptance criteria
- [ ] Every §19.6 refusal category is enforced and covered by an adversarial eval.
- [ ] High-risk output is blocked or rewritten; disclaimers/escalation injected where required.
- [ ] Safety verdict cannot be overridden by LLM content; safety_status recorded on output + ModelRun.
- [ ] 0 unsupported medical diagnosis/treatment outputs in the golden safety eval set; CI blocks on any safety-eval failure.

## Tests required
- Adversarial prompt suite (all §19.6 categories) + emergency-escalation test.
- Override-attempt test (LLM tries to bypass → still blocked); disclaimer-injection test.

## PRD references
§19.6 AI Safety Guardrails, §16.3 Safety Policy Engine, §22.2 safety evals, §8.2 (0 unsupported), FR-053/081.
