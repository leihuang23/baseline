# Architecture

Baseline is a local-first physiological decision-support system. The
architecture is deliberately conservative: deterministic code owns personal
health calculations, PostgreSQL owns personal evidence, curated RAG owns
external references, and the LLM layer is limited to explanation over bounded
structured inputs.

## Core Documents

- [System overview](system-overview.md)
- [Data model](data-model.md)
- [API contracts](api-contracts.md)
- [Model routing](model-routing.md)
- [Evaluation harness](evaluation.md)
- [Observability foundation](observability.md)
- [Synthetic data](synthetic-data.md)
- [OpenAPI snapshot](openapi.json)

Privacy and safety are documented separately because they are product
constraints, not appendices:

- [Privacy notes](../privacy/README.md)
- [Privacy data flow](../privacy/data-flow.md)
- [Safety boundary](../safety/README.md)
- [Failure modes](../safety/failure-modes.md)

## Architectural Invariants

- Health and lifestyle metrics are computed by versioned deterministic modules.
- Personal evidence is retrieved from SQL records and source references.
- External knowledge is curated, chunked, cited, and kept separate from personal
  evidence.
- Safety validation runs after generation and can block or rewrite output.
- Logs, traces, model-run records, docs, demo artifacts, and eval fixtures must
  not contain raw health samples, raw notes, secrets, or prompt payloads.
