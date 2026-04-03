"""Persistence layer — DuckDB checkpointer and FAISS vector store."""
from lattice.persistence.checkpointer import create_checkpointer
from lattice.persistence.vector_store import EMBEDDING_DIM, load_or_create_index

__all__ = ["create_checkpointer", "load_or_create_index", "EMBEDDING_DIM"]
