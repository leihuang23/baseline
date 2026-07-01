# P0-07: Evaluation harness scaffold + CI gate

**Phase:** 0 — Feasibility & foundations | **Depends on:** P0-02, P0-03 | **Parallelizable with:** P0-04, P0-06 | **Surface:** backend (`packages/eval`)

## Context (self-contained)
For Baseline, **the evaluation harness is part of MVP, not an afterthought** (§26.15). Later slices attach suites (deterministic feature tests, reasoning golden scenarios, LLM/safety/retrieval/privacy evals). This slice builds the reusable runner + reporting + CI gate so those suites plug in consistently.

## Goal
Create a scenario-driven eval harness that loads fixtures, runs registered suites, scores against expected properties, persists `EvaluationCase` results, and emits a machine- and human-readable report wired into CI.

## Scope
In:
- A suite/registry abstraction: each eval declares `scenario_name`, `input_fixture` (from `packages/fixtures`), `expected_properties`, and a scorer producing pass/fail + `failure_reason`.
- Support for eval *types* (deterministic, LLM-property, retrieval, safety, privacy, regression) even though most suites arrive later — seed with 1–2 trivial deterministic examples.
- Results persisted to the `EvaluationCase` table + a JSON/Markdown report artifact.
- `make eval` and a CI job that fails the build on any regression or safety-eval failure.
- LLM evals run against **mock/recorded** model responses by default (no live API in CI).

Out:
- The actual feature/reasoning/safety suites (added in P2-04, P3-03, P3-05, P5-02).
- The dashboard visualization (P5-03) — but emit results in a shape the dashboard can read.

## Deliverables
- `packages/eval/` harness (runner, registry, scorers, reporters) + `docs/architecture/evaluation.md`.

## Acceptance criteria
- [ ] A suite can be registered and executed by name; results write to `EvaluationCase` and to a report file.
- [ ] Report distinguishes eval types and lists failures with reasons.
- [ ] CI gate fails on any failing safety or regression eval.
- [ ] LLM-type evals use mocked responses; harness runs offline/deterministically.

## Tests required
- Harness self-test: a passing and a deliberately-failing sample suite produce correct pass/fail + report.
- CI-gate test: a failing safety eval returns nonzero exit.

## PRD references
§22 Evaluation Strategy, §26.15, §27.8–9, FR-091.
