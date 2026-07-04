"""Auditable ingestion pipeline for Baseline's curated knowledge corpus."""

from __future__ import annotations

import datetime as dt
import hashlib

from packages.knowledge.chunking import ChunkingConfig, chunk_text
from packages.knowledge.curation import validate_document
from packages.knowledge.embeddings import EmbeddingProvider, HashEmbeddingProvider
from packages.knowledge.models import (
    IngestionResult,
    KnowledgeChunkPayload,
    KnowledgeSourceDocument,
)
from packages.knowledge.store import KnowledgeVectorStore


class KnowledgeIngestionPipeline:
    """Validate, chunk, embed, and store curated external knowledge sources."""

    def __init__(
        self,
        store: KnowledgeVectorStore,
        embedder: EmbeddingProvider | None = None,
        chunking_config: ChunkingConfig | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder or HashEmbeddingProvider()
        self.chunking_config = chunking_config or ChunkingConfig()

    def ingest(self, document: KnowledgeSourceDocument) -> IngestionResult:
        validate_document(document)
        ingested_at = document.ingested_at or dt.datetime.now(dt.UTC)
        chunks = [
            KnowledgeChunkPayload(
                chunk_index=index,
                text=chunk,
                content_hash=_chunk_hash(
                    document.url_or_identifier,
                    document.version,
                    index,
                    chunk,
                ),
                embedding=self.embedder.embed(chunk),
            )
            for index, chunk in enumerate(chunk_text(document.content, self.chunking_config))
        ]
        return self.store.upsert_source(document, chunks, ingested_at)

    def remove(self, source_identifier: str) -> int:
        return self.store.remove_source(source_identifier, dt.datetime.now(dt.UTC))


def _chunk_hash(source_identifier: str, version: str, chunk_index: int, text: str) -> str:
    payload = f"{source_identifier}\n{version}\n{chunk_index}\n{text}".encode()
    return hashlib.sha256(payload).hexdigest()
