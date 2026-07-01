# P3-04: LLM orchestrator + structured outputs

**Phase:** 3 — Reasoning, briefing, safety | **Depends on:** P0-04, P0-06 | **Parallelizable with:** P3-02, P3-05 | **Surface:** backend

## Context (self-contained)
In Baseline the LLM **explains and summarizes bounded structured context — it does not compute features, invent data, or override safety.** Every call is logged as a `ModelRun` (provider, model, prompt_version, input/output hashes, tokens, cost, latency, safety_result) so recommendations are reproducible and cost-visible. Provider-agnostic; default Anthropic Claude (`claude-haiku-4-5` cheap tier, `claude-opus-4-8` strong tier).

## Goal
Build the provider-agnostic LLM orchestrator: prompt templating with the safety boundary, schema-valid structured outputs, model routing, retries/fallback, and full `ModelRun` telemetry — testable against mock/recorded responses.

## Scope
In:
- A thin provider interface + Anthropic implementation; pluggable for others; **no live calls in tests/CI** (mock/record).
- Prompt templates (versioned) that always include: product safety boundary, the structured feature/assessment object, retrieved evidence *only*, explicit-uncertainty requirement, no-diagnosis/treatment instruction, citation requirement for external knowledge, and a schema-valid-output requirement (§21.4).
- Structured output via Pydantic JSON schema with **validation + repair/retry**; on repeated schema failure, degrade gracefully (surface deterministic assessment without LLM prose).
- Model routing: cheap model for classification/summarization/simple explanation; strong model for complex longitudinal/planning (§21.3). Provider fallback on failure.
- `ModelRun` logging for every call (input/output **hashes**, not raw personal prompts, per §20.4); cost + latency captured; prompt_version + schema_version recorded.
- Enforce minimization: raw samples not sent when derived features suffice; respect consent flags (external_llm_enabled, raw_note_processing_enabled).

Out:
- The safety gate itself (P3-05) — orchestrator *calls* it as a post-step; briefing assembly (P3-06); knowledge retrieval (P5-02).

## Deliverables
- `baseline_api/llm/` (provider interface, router, prompt registry, structured-output validator, ModelRun logger).

## Acceptance criteria
- [ ] All prompts carry the safety boundary + require uncertainty + forbid diagnosis/fabrication; outputs are schema-validated.
- [ ] Schema-invalid output triggers retry/repair, then graceful degrade — never ships invalid JSON downstream.
- [ ] Every call writes a `ModelRun` with hashes (no raw PII), tokens, cost, latency, prompt/schema versions.
- [ ] Routing + provider fallback verified with mocks; consent/minimization respected.
- [ ] ≥90% schema-valid outputs in the automated (mocked) eval fixture.

## Tests required
- Mock-response tests: valid, invalid-then-repaired, invalid-then-degrade.
- Routing + fallback tests; ModelRun-logging + redaction test (no raw prompt persisted/logged); minimization/consent tests.

## PRD references
§21 LLM & Agent Design, §17 (model metadata), §20.4, §8.2 (≥90% schema-valid), FR-052/053, NFR-010/014.
