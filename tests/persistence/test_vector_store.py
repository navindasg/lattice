"""Tests for FAISS vector store factory."""
import pytest
import faiss

from lattice.persistence.vector_store import EMBEDDING_DIM, load_or_create_index


class TestLoadOrCreateIndex:
    def test_creates_new_index(self, tmp_path):
        path = str(tmp_path / "test.index")
        index = load_or_create_index(path)
        assert index is not None
        assert isinstance(index, faiss.Index)

    def test_new_index_has_correct_dimension(self, tmp_path):
        path = str(tmp_path / "test.index")
        index = load_or_create_index(path)
        assert index.d == EMBEDDING_DIM

    def test_dimension_matches_constant(self, tmp_path):
        path = str(tmp_path / "test.index")
        index = load_or_create_index(path, dim=EMBEDDING_DIM)
        assert index.d == EMBEDDING_DIM
        assert EMBEDDING_DIM == 1536

    def test_index_is_empty_on_creation(self, tmp_path):
        path = str(tmp_path / "test.index")
        index = load_or_create_index(path)
        assert index.ntotal == 0

    def test_persist_and_reload(self, tmp_path):
        """Create index, save it, reload it, verify dimension matches."""
        path = str(tmp_path / "test.index")

        # Create and save
        index = load_or_create_index(path)
        assert index.d == EMBEDDING_DIM

        # Reload from file
        index2 = load_or_create_index(path)
        assert index2.d == EMBEDDING_DIM

    def test_loads_existing_instead_of_creating_new(self, tmp_path):
        """load_or_create_index should load from file, not recreate."""
        path = str(tmp_path / "existing.index")

        # Create and write manually
        original = faiss.IndexFlatL2(EMBEDDING_DIM)
        faiss.write_index(original, path)

        # Load via factory
        loaded = load_or_create_index(path)
        assert loaded.d == EMBEDDING_DIM

    def test_dimension_mismatch_raises_value_error(self, tmp_path):
        """Loading an index with wrong dimension raises ValueError."""
        path = str(tmp_path / "small.index")

        # Create with dim=768
        small_index = faiss.IndexFlatL2(768)
        faiss.write_index(small_index, path)

        # Try to load expecting dim=1536 — should raise
        with pytest.raises(ValueError, match="dimension mismatch"):
            load_or_create_index(path, dim=1536)

    def test_custom_dimension(self, tmp_path):
        """Factory respects custom dim parameter."""
        path = str(tmp_path / "custom.index")
        custom_dim = 768
        index = load_or_create_index(path, dim=custom_dim)
        assert index.d == custom_dim
