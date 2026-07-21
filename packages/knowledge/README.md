# Knowledge

Curated external evidence corpus lives here. Personal time-series data must stay in SQL.

This package owns curated-corpus ingestion and retrieval behavior:

- required source metadata and explicit trust assignment;
- non-null title, author/org, source type, URL/identifier, license status,
  publication date, ingest time, version, and trust level on accepted sources;
- curation rejection for low-authority or uncited web/article content;
- personal-data boundary checks before chunking or embedding;
- deterministic chunking and offline embeddings for local ingestion;
- source supersede/removal semantics for vector-store chunks.

Superseding a source version marks the older source row as superseded but keeps
its chunks for audit history. Active matching excludes superseded and removed
source rows. Explicit source removal marks all matching source versions removed
and purges their chunks.

Query-time retrieval filters inactive sources and preserves source metadata for
downstream citation rendering.

## Starter Corpus

`starter_corpus.py` contains a tiny seed set for local smoke tests. Entries are
hand-authored summaries of public-domain U.S. federal government guidance:

- U.S. Department of Health and Human Services, *Physical Activity Guidelines for
  Americans, 2nd edition*.
- U.S. Department of Agriculture and U.S. Department of Health and Human
  Services, *Dietary Guidelines for Americans, 2020-2025*.

The starter content is external reference material only. It must not include raw
health samples, Apple Health/HealthKit provenance, user IDs, manual check-ins,
free-text notes, prompt/model-run payloads, or any other personal Baseline data.
