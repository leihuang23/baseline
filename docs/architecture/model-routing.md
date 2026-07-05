# Model Routing

Baseline uses models only after deterministic code has produced structured
features, readiness state, evidence, confidence, uncertainty, and safety flags.
The model layer explains bounded inputs; it does not calculate health metrics,
retrieve raw personal history, diagnose, prescribe, or decide whether safety
rules apply.

## Runtime Components

| Component | Location | Responsibility |
| --- | --- | --- |
| Router | `baseline_api.llm.router` | Chooses cheap, strong, or fallback model names from settings based on task type and constraints. |
| Factory | `baseline_api.llm.factory` | Creates provider clients from environment-backed settings. |
| Providers | `baseline_api.llm.providers` | Defines provider interface and local deterministic fallback behavior. |
| Prompts | `baseline_api.llm.prompts` | Builds minimized, structured prompt inputs. |
| Orchestrator | `baseline_api.llm.orchestrator` | Runs generation, validation, fallback, model-run logging, and metrics. |
| Validation | `baseline_api.llm.validation` | Enforces schema-valid explanation output and degraded responses. |
| Model-run logger | `baseline_api.llm.modelrun_logger` | Persists hashes, provider/model metadata, schema versions, token usage, cost, latency, and safety metadata. |

## Routing Policy

Settings live in `baseline_api.config.Settings`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `LLM_CHEAP_MODEL` | `deepseek-v4-pro` | Default low-cost model for simple explanation tasks. |
| `LLM_STRONG_MODEL` | `deepseek-v4-pro` | Higher-capability model slot for harder reasoning/explanation tasks. |
| `LLM_FALLBACK_MODEL` | `baseline-local-deterministic-v1` | Local degraded output path when external generation is unavailable or disallowed. |

The router is intentionally simple at this stage. The important product
constraint is not model cleverness; it is that every model call is:

- consent-gated when external processing is involved,
- minimized to structured derived data and source references,
- schema-validated,
- traced with hashes rather than raw prompt payloads,
- checked by the safety engine after generation.

## Data Sent To Models

Allowed model context is built from:

- derived daily features,
- readiness assessment fields,
- selected goal tradeoffs,
- safe memory summaries with `sensitive_fields_excluded` honored,
- retrieved public knowledge citations when enabled,
- explicit safety and output-schema instructions.

Disallowed model context includes:

- raw HealthKit samples,
- arbitrary normalized row dumps,
- raw check-in notes or free-text note references,
- sexual-health notes,
- secrets or tokens,
- raw prompt payload persistence.

## Failure And Fallback

If model generation fails, exceeds budget, returns invalid schema, or is not
allowed by privacy settings, Baseline emits a degraded but structured response.
The degraded response still includes evidence, uncertainty, and safety status,
and the trace records the degraded stage instead of hiding the failure.
