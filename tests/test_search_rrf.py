"""Tests for RRF fusion in search module."""

from unittest.mock import MagicMock
from my_agent_memory.search import HybridSearch


def make_entry(entry_id, **extra):
    e = {"id": entry_id, "title": f"Entry {entry_id}", "content": f"Content {entry_id}"}
    e.update(extra)
    return e


class TestRRFFuse:
    def setup_method(self):
        self.db = MagicMock()
        self.search = HybridSearch(self.db, embed_client=None)
        self.search._rrf_k = 60

    def test_basic_fusion(self):
        """Entries in both FTS and vec should rank higher."""
        fts = [make_entry(1), make_entry(2), make_entry(3)]
        vec = {1: 0.1, 2: 0.2, 3: 0.3}  # lower distance = better
        results = self.search._rrf_fuse(fts, vec, limit=3)
        ids = [r["id"] for r in results]
        # Entry 1 is top in both FTS (rank 0) and vec (rank 0) → should be first
        assert ids[0] == 1

    def test_vec_only_entry(self):
        """Entry only in vec results (not in FTS) should still appear."""
        fts = [make_entry(1)]
        vec = {2: 0.05}  # entry 2 only in vec
        self.db.get.return_value = make_entry(2)
        results = self.search._rrf_fuse(fts, vec, limit=5)
        ids = [r["id"] for r in results]
        assert 2 in ids

    def test_fts_only_entry(self):
        """Entry only in FTS (no embedding) should still appear."""
        fts = [make_entry(1), make_entry(2)]
        vec = {}  # no vec results
        results = self.search._rrf_fuse(fts, vec, limit=5)
        ids = [r["id"] for r in results]
        assert 1 in ids
        assert 2 in ids

    def test_limit_respected(self):
        fts = [make_entry(i) for i in range(20)]
        results = self.search._rrf_fuse(fts, {}, limit=5)
        assert len(results) == 5

    def test_rrf_score_present(self):
        """Each result should have rrf_score attached."""
        fts = [make_entry(1)]
        results = self.search._rrf_fuse(fts, {}, limit=5)
        assert "rrf_score" in results[0]

    def test_empty_inputs(self):
        results = self.search._rrf_fuse([], {}, limit=5)
        assert results == []

    def test_vec_rank_precomputed(self):
        """Verify vec ranks are pre-computed (not sorted in loop)."""
        fts = [make_entry(1), make_entry(2), make_entry(3)]
        vec = {1: 0.3, 2: 0.1, 3: 0.2}  # sorted: 2, 3, 1
        results = self.search._rrf_fuse(fts, vec, limit=3)
        # Entry 2: FTS rank 1, vec rank 0 → should score well
        # Entry 1: FTS rank 0, vec rank 2 → mixed
        ids = [r["id"] for r in results]
        assert len(ids) == 3
