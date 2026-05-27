"""Tests for TagGraph co-occurrence system."""

import os
os.environ["HERMES_AGENT_ID"] = "test"

from my_agent_memory.store import MultiAgentStore


def make_store():
    return MultiAgentStore(db_path=":memory:", agent_id="test", config={})


class TestTagGraph:
    def test_update_cooccurrence_creates_pairs(self):
        store = make_store()
        store.tag_graph.update_cooccurrence(["python", "django", "flask"])
        # 3 tags → 3 pairs: (django,flask), (django,python), (flask,python)
        stats = store.tag_graph.get_tag_stats()
        assert stats["total_pairs"] == 3
        store.close()

    def test_update_cooccurrence_increments(self):
        store = make_store()
        store.tag_graph.update_cooccurrence(["python", "django"])
        store.tag_graph.update_cooccurrence(["python", "django"])
        store.tag_graph.update_cooccurrence(["python", "django"])
        related = store.tag_graph.get_related_tags("python")
        assert len(related) == 1
        assert related[0]["tag"] == "django"
        assert related[0]["count"] == 3
        store.close()

    def test_get_related_tags(self):
        store = make_store()
        store.tag_graph.update_cooccurrence(["python", "django"])
        store.tag_graph.update_cooccurrence(["python", "flask"])
        store.tag_graph.update_cooccurrence(["python", "fastapi"])
        related = store.tag_graph.get_related_tags("python")
        assert len(related) == 3
        tags = {r["tag"] for r in related}
        assert tags == {"django", "flask", "fastapi"}
        store.close()

    def test_get_related_tags_bidirectional(self):
        store = make_store()
        store.tag_graph.update_cooccurrence(["python", "django"])
        # Query from both directions
        related_from_python = store.tag_graph.get_related_tags("python")
        related_from_django = store.tag_graph.get_related_tags("django")
        assert related_from_python[0]["tag"] == "django"
        assert related_from_django[0]["tag"] == "python"
        store.close()

    def test_expand_query(self):
        store = make_store()
        store.tag_graph.update_cooccurrence(["python", "django", "flask"])
        store.tag_graph.update_cooccurrence(["python", "fastapi"])
        expanded = store.tag_graph.expand_query(["python"])
        assert "python" in expanded
        assert "django" in expanded
        assert "flask" in expanded
        assert "fastapi" in expanded
        store.close()

    def test_expand_query_deduplicates(self):
        store = make_store()
        store.tag_graph.update_cooccurrence(["a", "b"])
        store.tag_graph.update_cooccurrence(["a", "c"])
        expanded = store.tag_graph.expand_query(["a", "b"])
        # "a" appears in original and as related of "b", should be deduped
        assert expanded.count("a") == 1
        store.close()

    def test_cooccurrence_sorted_order(self):
        """tag_a < tag_b lexicographically."""
        store = make_store()
        store.tag_graph.update_cooccurrence(["zebra", "alpha"])
        stats = store.tag_graph.get_tag_stats()
        pair = stats["top_pairs"][0]
        assert pair["tag_a"] == "alpha"
        assert pair["tag_b"] == "zebra"
        store.close()

    def test_single_tag_no_pairs(self):
        store = make_store()
        store.tag_graph.update_cooccurrence(["python"])
        stats = store.tag_graph.get_tag_stats()
        assert stats["total_pairs"] == 0
        store.close()

    def test_save_updates_graph(self):
        """Saving an entry with multiple tags should update the graph."""
        store = make_store()
        store.save("Test", tags=["python", "django", "flask"])
        stats = store.tag_graph.get_tag_stats()
        assert stats["total_pairs"] == 3
        store.close()

    def test_tag_graph_stats(self):
        store = make_store()
        store.tag_graph.update_cooccurrence(["a", "b"])
        store.tag_graph.update_cooccurrence(["c", "d"])
        stats = store.tag_graph.get_tag_stats()
        assert stats["total_pairs"] == 2
        assert len(stats["top_pairs"]) == 2
        store.close()
