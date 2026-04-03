"""FAISS vector store factory.

Creates or loads a FAISS index with the correct embedding dimension.
Phase 1: index is initialized empty; vectors are added in Phase 4+.

EMBEDDING_DIM = 1536 matches text-embedding-3-small. Adjust if switching
to a different embedding model (e.g., nomic-embed-text uses dim=768).
"""
from __future__ import annotations

import os

import faiss
import structlog

log = structlog.get_logger(__name__)

EMBEDDING_DIM = 1536  # matches text-embedding-3-small; configurable via dim param


def load_or_create_index(
    path: str = ".data/faiss.index",
    dim: int = EMBEDDING_DIM,
) -> faiss.Index:
    """Load a FAISS index from file, or create a new one if the file doesn't exist.

    Args:
        path: File path for the FAISS index. Parent directory is created automatically.
        dim: Embedding dimension. Must match the dimension of the stored index if loading.

    Returns:
        A faiss.Index with the specified dimension.

    Raises:
        ValueError: If the loaded index has a different dimension than `dim`.
    """
    if os.path.exists(path):
        index = faiss.read_index(path)
        if index.d != dim:
            raise ValueError(
                f"FAISS index dimension mismatch: stored index has d={index.d}, "
                f"but expected dim={dim}. Delete {path!r} to rebuild."
            )
        log.info("vector_store_loaded", path=path, dim=index.d, ntotal=index.ntotal)
        return index

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    index = faiss.IndexFlatL2(dim)
    faiss.write_index(index, path)
    log.info("vector_store_created", path=path, dim=dim)

    return index
