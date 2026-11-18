"""Persistent Chroma vector store via LangChain (guide 7-1). Build in batch; the dashboard only queries."""

from __future__ import annotations

from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from src.rag.document_processor import Chunk

COLLECTION = "dart_filings"


MODEL_FILE = "embedding_model.txt"


class EmbeddingModelMismatch(RuntimeError):
    pass


def open_store(path: Path, embeddings: Embeddings, model: str) -> Chroma:
    """Cosine space so relevance = 1 - cosine distance, comparable across runs for the refusal threshold.

    The embedding model that built the store is recorded next to it. Vectors from different models are
    not comparable (and some share a dimension, so nothing would fail loudly), so a mismatch is an error.
    """
    path.mkdir(parents=True, exist_ok=True)
    marker = path / MODEL_FILE
    if marker.exists():
        built_with = marker.read_text(encoding="utf-8").strip()
        if built_with != model:
            raise EmbeddingModelMismatch(
                f"vector store at {path} was built with '{built_with}', but the configured model is '{model}'. "
                "Use the same model, or delete the vector store folder and rebuild it.")
    else:
        marker.write_text(model, encoding="utf-8")
    return Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=str(path),
        collection_metadata={"hnsw:space": "cosine"},
    )


def existing_ids(store: Chroma, ids: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(ids), 500):
        found.update(store.get(ids=ids[i:i + 500], include=[])["ids"])
    return found


def add_chunks(store: Chroma, chunks: list[Chunk], batch: int = 100) -> int:
    """Embed and add chunks whose chunk_id is not stored yet (re-runs only pay for new chunks)."""
    have = existing_ids(store, [c.chunk_id for c in chunks])
    new = [c for c in chunks if c.chunk_id not in have]
    for i in range(0, len(new), batch):
        part = new[i:i + batch]
        store.add_texts(texts=[c.text for c in part], metadatas=[c.metadata for c in part],
                        ids=[c.chunk_id for c in part])
    return len(new)


def delete_document(store: Chroma, doc_id: str) -> None:
    """Drop every vector of a filing whose text changed, so re-chunking leaves no stale chunks."""
    ids = store.get(where={"doc_id": doc_id}, include=[])["ids"]
    if ids:
        store.delete(ids=ids)
