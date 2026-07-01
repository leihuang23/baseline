# P4-03: Feedback & outcome loop

**Phase:** 4 — Memory & feedback | **Depends on:** P3-06 | **Parallelizable with:** P4-01 | **Surface:** backend

## Context (self-contained)
Baseline closes the loop: the user rates recommendations, records what they actually did, and next-day outcomes are captured. Crucial safety rule: **feedback improves personal memory and evaluation — it must NOT silently mutate safety rules.** Repeated contradicting feedback should be surfaced, not quietly obeyed.

## Goal
Implement feedback capture, action-taken + next-day outcome tracking, and the wiring that routes feedback into memory + evaluation (never into safety-rule mutation), including surfacing persistent contradictions.

## Scope
In:
- `POST /v1/recommendations/{id}/feedback` (§17.6): rating, action_taken, reason, outcome_notes → feedback_id, memory_update_status, eval_queue_status.
- Rate recommendations; record actual action taken (FR-085/086); capture next-day outcome signals and link them to the prior recommendation (FR-087).
- Route feedback to personal memory + the eval queue; **explicitly forbid** feedback from mutating safety rules (FR-088).
- Support "this was wrong because…" structured feedback (FR-089); detect + surface when repeated feedback contradicts current reasoning (FR-090).
- Feed outcomes into the eval harness (usefulness tracking) and memory (P4-01/02).

Out:
- The dashboard visualization (P5-03); UI for feedback (fold into P3-08 follow-up or a later settings slice).

## Deliverables
- `baseline_api/feedback/` service + endpoint + memory/eval wiring.

## Acceptance criteria
- [ ] Feedback + action-taken + next-day outcome captured and linked to the recommendation.
- [ ] Feedback updates memory + enqueues eval; a test proves safety rules are **not** mutated by feedback.
- [ ] Persistent contradicting feedback is surfaced (flag/notification), not silently applied.
- [ ] "This was wrong because…" captured as structured signal.

## Tests required
- Feedback→memory/eval routing test; **safety-immutability** test (feedback cannot change safety policy).
- Outcome-linking test; repeated-contradiction surfacing test.

## PRD references
§17.6, §12.11 FR-085–090, §28 (usefulness risk mitigation), user stories 49–51.
