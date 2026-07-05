# GitHub Agent Workflow

Baseline now uses GitHub as the queue, review, and gatekeeping surface for
post-implementation fixes.

## Flow

1. File a small GitHub issue for each manual review finding or test failure.
2. Include the expected outcome, relevant file paths, and the smallest useful
   verification command. Redact health data, secrets, and prompt payloads.
3. Assign the issue to Copilot when it is small enough for one focused PR.
4. Review the Copilot PR like a human PR: inspect the diff, read CI, request
   changes when needed, and merge only after required checks pass.
5. For follow-up context, comment on the PR rather than the original issue.
   Copilot receives the issue details at assignment time, but later issue
   comments are not part of the active agent session.

## Required Checks

The `CI` workflow is the merge gate:

- Python API, eval, and docs: format check, lint, mypy, DB-backed pytest,
  Alembic migration, eval, and docs consistency.
- Dashboard tests: `npm test --prefix apps/dashboard`.
- iOS package tests: `swift test --package-path apps/ios`.

The Copilot setup workflow prepares the cloud agent environment with Python,
uv, Node, and validation command availability. It is intentionally lighter than
CI so agent startup does not spend its budget running the full suite before it
has made a change.

## Assignment Prompt

When assigning an issue to Copilot, add optional guidance like:

```text
Keep the fix scoped to this issue. Do not use the historical tasks folder as
current requirements. Preserve Baseline privacy/safety guardrails. Add or update
a regression test if behavior changes. Run the smallest relevant check and let
CI run the full matrix.
```

## Manual Review Rules

- Do not merge Copilot PRs on green CI alone when they touch privacy, safety,
  reasoning, data deletion/export, logging, audit events, migrations, or LLM
  orchestration.
- Prefer one issue per finding. Split broad review notes before assigning them.
- Treat Copilot code review as advisory feedback; it does not replace required
  human review.
