"""Hybrid search pipeline — FTS5 + vector similarity + RRF fusion.

Strategy:
  - >= 100 entries: FTS5 pre-filter (top 50) → vector re-rank (top 50 → top 20) → RRF fuse → top 10
  - < 100 entries: FTS5 (all) + vector (all) → RRF fuse → top 10
  - Entries without embedding: skip in vector path (FTS5-only), not scored as zero.
"""

from typing import Optional


class HybridSearch:
    """Orchestrates FTS5 and vector search with RRF fusion."""

    def __init__(self, db, embed_client=None):
        self.db = db
        self.embed_client = embed_client
        self._rrf_k = 60  # RRF smoothing constant

    def search(
        self,
        query: str,
        agent_id: str = "*",
        limit: int = 10,
        scope: str = None,
        project: str = None,
        fts_weight: float = 0.5,
        vec_weight: float = 0.5,
    ) -> list[dict]:
        """Hybrid search: FTS5 + vector with RRF fusion.

        Args:
            query: Search query string.
            agent_id: Agent ID for visibility filtering.
            limit: Max results to return.
            scope: Optional scope filter.
            project: Optional project filter.
            fts_weight: Weight of FTS5 results in RRF (0-1).
            vec_weight: Weight of vector results in RRF (0-1).

        Returns:
            List of entry dicts sorted by relevance.
        """
        total_count = self._total_active_count()

        if total_count >= 100:
            return self._two_stage_search(query, agent_id, limit, scope, project, fts_weight, vec_weight)
        else:
            return self._full_fusion_search(query, agent_id, limit, scope, project, fts_weight, vec_weight)

    def _total_active_count(self) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) as n FROM memory_entries WHERE deleted_at IS NULL AND state != 'archived'"
        )
        return row["n"] if row else 0

    def _two_stage_search(
        self, query: str, agent_id: str, limit: int,
        scope: str, project: str, fts_weight: float, vec_weight: float,
    ) -> list[dict]:
        """Two-stage: FTS5 pre-filter → vector re-rank → RRF."""
        # Stage 1: FTS5 pre-filter (top 50)
        fts_results = self.db.search(
            query, agent_id=agent_id, limit=50, scope=scope, project=project
        )
        if not fts_results:
            return []

        # Stage 2: Vector re-rank within FTS5 candidates
        vec_scores = {}
        if self.db.has_vector and self.embed_client:
            query_vec = self.embed_client.embed(query)
            if query_vec:
                candidate_ids = [r["id"] for r in fts_results]
                vec_rows = self.db.vector_search(query_vec, candidate_ids=candidate_ids, limit=50)
                for entry_id, distance in vec_rows:
                    vec_scores[entry_id] = distance

        # Stage 3: RRF fusion
        return self._rrf_fuse(fts_results, vec_scores, limit, fts_weight, vec_weight)

    def _full_fusion_search(
        self, query: str, agent_id: str, limit: int,
        scope: str, project: str, fts_weight: float, vec_weight: float,
    ) -> list[dict]:
        """Full fusion: FTS5 all + vector all → RRF."""
        # FTS5 search
        fts_results = self.db.search(
            query, agent_id=agent_id, limit=100, scope=scope, project=project
        )

        # Vector search
        vec_scores = {}
        if self.db.has_vector and self.embed_client:
            query_vec = self.embed_client.embed(query)
            if query_vec:
                vec_rows = self.db.vector_search(query_vec, limit=100)
                for entry_id, distance in vec_rows:
                    vec_scores[entry_id] = distance

        # RRF fusion
        return self._rrf_fuse(fts_results, vec_scores, limit, fts_weight, vec_weight)

    def _rrf_fuse(
        self,
        fts_results: list[dict],
        vec_scores: dict[int, float],
        limit: int,
        fts_weight: float = 0.5,
        vec_weight: float = 0.5,
    ) -> list[dict]:
        """Reciprocal Rank Fusion: combine FTS5 and vector rankings.

        RRF(entry) = fts_weight / (k + rank_fts) + vec_weight / (k + rank_vec)

        Entries without embedding: only FTS5 score applies (vec rank = infinity → vec term = 0).
        """
        k = self._rrf_k

        # Build entry map from FTS results
        entry_map = {r["id"]: r for r in fts_results}

        # Compute RRF scores
        rrf_scores = {}
        for rank, entry in enumerate(fts_results):
            entry_id = entry["id"]
            fts_term = fts_weight / (k + rank + 1)

            vec_term = 0.0
            if entry_id in vec_scores:
                # Vec scores are distances, need ranking. Build rank from vec_scores.
                # Sort vec_scores by distance ascending to get ranks.
                sorted_vec = sorted(vec_scores.items(), key=lambda x: x[1])
                for vec_rank, (vid, vdist) in enumerate(sorted_vec):
                    if vid == entry_id:
                        vec_term = vec_weight / (k + vec_rank + 1)
                        break

            rrf_scores[entry_id] = fts_term + vec_term

        # Add entries from vector results that weren't in FTS results
        sorted_vec = sorted(vec_scores.items(), key=lambda x: x[1])
        for vec_rank, (entry_id, vdist) in enumerate(sorted_vec):
            if entry_id not in entry_map:
                vec_term = vec_weight / (k + vec_rank + 1)
                rrf_scores[entry_id] = vec_term  # FTS term = 0
                # Load entry from DB
                entry = self.db.get(entry_id)
                if entry:
                    entry_map[entry_id] = entry

        # Sort by RRF score descending
        sorted_entries = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        # Build result list
        results = []
        for entry_id, rrf_score in sorted_entries[:limit]:
            entry = entry_map.get(entry_id, self.db.get(entry_id))
            if entry:
                entry["rrf_score"] = round(rrf_score, 4)
                results.append(entry)

        return results

    def search_fts_only(
        self, query: str, agent_id: str = "*", limit: int = 10,
        scope: str = None, project: str = None,
    ) -> list[dict]:
        """FTS5-only search (no vector fusion)."""
        return self.db.search(query, agent_id=agent_id, limit=limit, scope=scope, project=project)

    def search_vec_only(
        self, query: str, limit: int = 10,
    ) -> list[dict]:
        """Vector-only search (no FTS5). Useful for semantic similarity queries."""
        if not self.db.has_vector or not self.embed_client:
            return []

        query_vec = self.embed_client.embed(query)
        if not query_vec:
            return []

        vec_rows = self.db.vector_search(query_vec, limit=limit)
        results = []
        for entry_id, distance in vec_rows:
            entry = self.db.get(entry_id)
            if entry:
                entry["vec_distance"] = round(distance, 4)
                results.append(entry)
        return results
