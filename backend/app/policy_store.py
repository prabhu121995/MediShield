"""
Policy vector store (Qdrant, embedded local mode) and PDF ingestion (Docling).

- ingest_pdf(): Docling converts the PDF, HybridChunker splits it by section,
  and each chunk is embedded and stored with its source file name.
- PolicyStore.search(): dense similarity search, used by the policy agent.

Dense embeddings only for now. The notebook's sparse BM25 hybrid can be added
later as a second named vector.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from app import config


class PolicyStore:
    def __init__(self, location: str | None = None, collection: str | None = None,
                 embed_model: str | None = None):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self.collection = collection or config.POLICY_COLLECTION
        self._embed_model = embed_model or config.EMBED_MODEL
        self._embedder = None
        if location is None:
            self.client = QdrantClient(path=str(config.QDRANT_PATH))
        else:
            self.client = QdrantClient(location) if location == ":memory:" else QdrantClient(path=location)
        self._Distance, self._VectorParams = Distance, VectorParams

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is None:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding(model_name=self._embed_model)
        return [vec.tolist() for vec in self._embedder.embed(texts)]

    def _ensure(self, dim: int) -> None:
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config=self._VectorParams(size=dim, distance=self._Distance.COSINE))

    def add(self, texts: list[str], source: str) -> int:
        from qdrant_client.models import PointStruct

        if not texts:
            return 0
        vectors = self._embed(texts)
        self._ensure(len(vectors[0]))
        points = [PointStruct(id=str(uuid.uuid4()), vector=v, payload={"text": t, "source": source})
                  for t, v in zip(texts, vectors)]
        self.client.upsert(self.collection, points)
        return len(points)

    def count(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.count(self.collection).count

    def search(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        if self.count() == 0:
            return []
        vector = self._embed([query])[0]
        hits = self.client.query_points(self.collection, query=vector, limit=k).points
        return [{"text": h.payload["text"], "source": h.payload["source"], "score": float(h.score)}
                for h in hits]


def ingest_pdf(pdf_path: str | Path, filename: str, store: PolicyStore) -> int:
    from docling.chunking import HybridChunker
    from docling.document_converter import DocumentConverter
    from transformers import AutoTokenizer

    document = DocumentConverter().convert(str(pdf_path)).document
    tokenizer = AutoTokenizer.from_pretrained(config.EMBED_MODEL)
    chunker = HybridChunker(tokenizer=tokenizer, max_tokens=config.POLICY_CHUNK_TOKENS, merge_peers=True)
    texts = [chunker.serialize(chunk=c) for c in chunker.chunk(dl_doc=document)]
    return store.add(texts, source=filename)


_store: PolicyStore | None = None


def get_policy_store() -> PolicyStore:
    global _store
    if _store is None:
        _store = PolicyStore()
    return _store


def set_policy_store(store: PolicyStore | None) -> None:
    global _store
    _store = store
