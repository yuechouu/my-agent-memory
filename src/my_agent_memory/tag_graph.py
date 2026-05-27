"""TagGraph — tag co-occurrence relationships for associative retrieval.

Tracks which tags appear together in entries, enabling:
  - Query expansion: "python" → also search "django", "flask"
  - Tag suggestion: based on co-occurrence patterns
  - Tag relationship exploration: what tags relate to "docker"?
"""

from __future__ import annotations

__all__ = ["TagGraph"]


class TagGraph:
    """Manages tag co-occurrence relationships."""

    def __init__(self, db):
        self.db = db

    def update_cooccurrence(self, tags: list[str]):
        """Update co-occurrence matrix for a set of tags.

        Called when a new entry is saved with tags.
        For each pair of tags, increment co_occurrence_count.
        Tags are stored in sorted order (tag_a < tag_b) to avoid duplicates.
        """
        if not tags or len(tags) < 2:
            return

        normalized = sorted(set(t.strip().lower() for t in tags if t.strip()))
        if len(normalized) < 2:
            return

        for i in range(len(normalized)):
            for j in range(i + 1, len(normalized)):
                a, b = normalized[i], normalized[j]
                self.db.execute(
                    """INSERT INTO tag_relations (tag_a, tag_b, co_occurrence_count, last_seen)
                       VALUES (?, ?, 1, datetime('now'))
                       ON CONFLICT(tag_a, tag_b)
                       DO UPDATE SET co_occurrence_count = co_occurrence_count + 1,
                                     last_seen = datetime('now')""",
                    (a, b),
                )
        self.db.commit()

    def get_related_tags(self, tag: str, limit: int = 10) -> list[dict]:
        """Find tags that co-occur with the given tag.

        Returns: [{"tag": "django", "count": 15}, ...] sorted by count desc.
        """
        tag = tag.strip().lower()
        rows = self.db.fetchall(
            """SELECT tag_b AS tag, co_occurrence_count AS count
               FROM tag_relations WHERE tag_a = ?
               UNION ALL
               SELECT tag_a AS tag, co_occurrence_count AS count
               FROM tag_relations WHERE tag_b = ?
               ORDER BY count DESC LIMIT ?""",
            (tag, tag, limit),
        )
        return [{"tag": r["tag"], "count": r["count"]} for r in rows]

    def expand_query(self, tags: list[str], max_expansion: int = 3) -> list[str]:
        """Given query tags, expand with related tags for better recall.

        Returns: original tags + related tags (deduplicated).
        """
        if not tags:
            return []

        original = set(t.strip().lower() for t in tags if t.strip())
        expanded = set(original)

        for tag in original:
            related = self.get_related_tags(tag, limit=max_expansion)
            for r in related:
                expanded.add(r["tag"])

        return sorted(expanded)

    def get_tag_stats(self) -> dict:
        """Get overall tag graph statistics."""
        total_pairs = self.db.fetchone(
            "SELECT COUNT(*) as n FROM tag_relations"
        )["n"]
        top_pairs = self.db.fetchall(
            """SELECT tag_a, tag_b, co_occurrence_count
               FROM tag_relations ORDER BY co_occurrence_count DESC LIMIT 10"""
        )
        return {
            "total_pairs": total_pairs,
            "top_pairs": [
                {"tag_a": r["tag_a"], "tag_b": r["tag_b"], "count": r["co_occurrence_count"]}
                for r in top_pairs
            ],
        }
