# Failure Modes And Degraded Behavior

Baseline should fail visibly and conservatively. Missing data, stale data,
provider failures, retrieval gaps, schema failures, and safety rewrites must be
traceable; they must not be hidden behind confident prose.

## Degraded Behavior Matrix

| Failure mode | Expected behavior | User-facing posture | Operator reference |
| --- | --- | --- | --- |
| Health sync fails or is stale | Preserve existing records, mark data freshness degraded, and avoid fabricating current-day inputs. | Explain stale or missing source data and lower confidence. | `docs/runbooks/sync-failures.md` |
| Backfill/data quality issue | Record data-quality flags and keep derived features versioned. | Surface uncertainty and ask for missing context when needed. | `baseline_api.ingestion.data_quality` |
| Feature computation fails | Do not ask the LLM to compute replacement metrics. Use degraded structured output or fail the job. | State that analysis could not be completed from available features. | `baseline_api.features.worker` |
| External knowledge retrieval unavailable | Continue with personal evidence only when safe. | Omit external citations and disclose that public-reference retrieval was unavailable. | `baseline_api.retrieval` |
| LLM provider failure | Use fallback/degraded output path and record provider failure metadata. | Provide bounded deterministic explanation or a retry-safe failure message. | `docs/runbooks/model-provider-failures.md` |
| LLM output schema invalid | Reject invalid output, use degraded response, and record schema failure. | Avoid displaying malformed or unvalidated text. | `docs/runbooks/schema-validation-failures.md` |
| Cost budget exceeded | Stop or route to fallback depending on configured policy. | Prefer deterministic summary over an unbounded external call. | `docs/runbooks/cost-budget-exceeded.md` |
| Safety policy violation | Block, rewrite, or escalate the generated text. | Show wellness boundary language and clinician/emergency escalation when appropriate. | `baseline_api.safety.engine` |
| Privacy deletion failure | Keep audit trail and retry/repair until owned rows are removed. | Do not claim deletion succeeded until verified. | `docs/runbooks/deletion-failures.md` |
| Dashboard real mode unauthenticated | Render the auth gate and no operational data. | Do not expose operator data outside a host-provided read-only context. | `apps/dashboard/README.md` |

## Design Rules

- Never use the LLM to fill missing measurements.
- Never increase recommendation intensity when uncertainty or hard safety flags
  are present.
- Never merge personal evidence and external citations into an ambiguous source.
- Never persist raw prompt payloads or raw health samples to logs.
- Never mark demo output as public-safe until synthetic/demo leak checks pass.

## Trace Requirements

A degraded pipeline should still produce enough trace metadata to explain:

- which stage degraded,
- what evidence was available,
- what uncertainty changed,
- whether external knowledge was included,
- which model path or fallback was used,
- what safety verdict was applied.
