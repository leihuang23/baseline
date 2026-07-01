# P4-01: Memory compiler — daily & weekly summaries

**Phase:** 4 — Memory & feedback | **Depends on:** P3-06 | **Parallelizable with:** P4-03 | **Surface:** backend

## Context (self-contained)
Baseline maintains **compressed personal memory** so reasoning uses recent summaries instead of re-reading all raw history (avoiding context explosion). Memory must **separate observation from hypothesis**, carry confidence + source references, and exclude sensitive raw notes by default. Summaries are structured, versioned, and source-linked — not free-form LLM musings.

## Goal
Implement daily and weekly memory-summary generation: structured summaries with observations vs hypotheses, confidence, evidence/source refs, sensitive-field exclusion, and correction/deletion — plus wiring so the reasoning engine prefers recent summaries.

## Scope
In:
- Daily structured summary from the day's features/assessment/outcome (FR-061).
- Weekly summary compiled from daily records (FR-062): training/recovery arcs, notable patterns.
- **Observation vs hypothesis** separation; each item carries confidence + supporting source_refs (FR-064/065).
- Exclude sensitive raw notes from long-term memory unless the user opts in (FR-066); memory compaction preserves source references for auditability (FR-069).
- Correction + deletion of memories (FR-067) with audit events.
- Reasoning integration: `ReadinessAssessment` consumes recent summaries before long raw history (FR-068) — provide the accessor P3-02 uses.
- LLM (via P3-04) may draft prose, but structure/confidence/source-refs are deterministic scaffolding; safety gate applies.

Out:
- Monthly/quarterly (P4-02); feedback loop (P4-03); memory UI.

## Deliverables
- `baseline_api/memory/` (daily + weekly compilers) + `MemorySummary` persistence + recent-summary accessor.

## Acceptance criteria
- [ ] Daily + weekly summaries generated with observation/hypothesis separation, confidence, and source_refs.
- [ ] Sensitive raw notes excluded by default; source references preserved for audit.
- [ ] Correction/deletion work + audited; reasoning can fetch recent summaries.
- [ ] Deterministic scaffolding (structure/confidence/refs not invented by the LLM).

## Tests required
- Longitudinal fixture: daily→weekly compaction correctness; observation-vs-hypothesis tagging test.
- Sensitive-exclusion test; correction/deletion test; source-ref-preservation test.

## PRD references
§12.8 FR-061–069, §16.3 Memory Compiler, §26.11/12, user stories 34–40.
