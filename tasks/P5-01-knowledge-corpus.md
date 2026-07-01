# P5-01: Curated knowledge corpus ingestion

**Phase:** 5 — Knowledge retrieval & eval dashboard (V1) | **Depends on:** P0-02 | **Parallelizable with:** P5-03 | **Surface:** backend (`packages/knowledge`)

## Context (self-contained)
This is the **only** place vector RAG belongs in Baseline: a **curated external-knowledge corpus** (exercise physiology, recovery, nutrition, HR-zone references) — never personal health data. Sources carry metadata, trust level, and versioning; low-authority/uncited web content is rejected by default. This separation (SQL for personal data, RAG for external knowledge) is a headline architectural decision (§26.3/4).

## Goal
Build an auditable ingestion pipeline for a curated corpus: source metadata, trust levels, versioning, removal, chunking + embedding, and default-reject of low-authority content.

## Scope
In:
- `KnowledgeSource` ingestion: title, author/org, source_type, url/identifier, license_status, published_at, ingested_at, version, trust_level (FR-071/075).
- Curation gate: reject low-authority or uncited web content by default; require explicit trust assignment (FR-076).
- Chunking + embedding into a vector store (pgvector or a dedicated store) with source metadata attached to every chunk.
- Source versioning + removal (re-ingest supersedes; removal purges chunks) (FR-075).
- Keep corpus + tooling in `packages/knowledge`; seed with a small, license-clear starter set.
- Explicit boundary check: personal data must never enter this corpus.

Out:
- Query-time retrieval + citation rendering (P5-02); assistant integration (already seamed in P3-07).

## Deliverables
- `packages/knowledge/` ingestion pipeline + vector store schema/migration + curation gate + starter corpus.

## Acceptance criteria
- [ ] Sources ingest with full metadata + trust_level; low-authority content rejected by default.
- [ ] Chunks retain source metadata; versioning supersedes and removal purges.
- [ ] No personal data path into the corpus (enforced + tested).
- [ ] Starter corpus is license-clear and documented.

## Tests required
- Ingestion + metadata-retention tests; curation-gate rejection test; version-supersede + removal-purge tests.
- Boundary test: attempting to ingest personal data is rejected.

## PRD references
§12.9 FR-070–076, §26.4, §16.3 Retrieval Layer, §28 (RAG-misapplication mitigation).
