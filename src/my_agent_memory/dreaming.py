"""Dreaming Engine — lifecycle automation for the three-tier memory system.

Responsibilities:
  1. Recalculate scores for all active entries
  2. Apply state transitions (raw→promoted→hot, demote, archive, purge)
  3. Detect and record conflicts in shared/project scope
  4. Trigger hot layer rebuild after transitions
  5. Log dreaming run details

Scheduled via cron (every 6h) or manually via CLI/API.
Pinned entries (is_pinned=True) are skipped for demote/archive transitions.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from my_agent_memory.scoring import compute_scores_for_entries
from my_agent_memory.memory_types import get_type_config

__all__ = ["DreamingEngine"]

logger = logging.getLogger("my-agent-memory.dreaming")


class DreamingEngine:
    """Manages the memory lifecycle from raw → promoted → hot ⇄ demote → archived → purge."""

    def __init__(
        self,
        db,
        hot_layer=None,
        embed_client=None,
        scoring_config: dict = None,
    ):
        self.db = db
        self.hot_layer = hot_layer
        self.embed_client = embed_client
        self.scoring_config = scoring_config or {}

    def run(
        self,
        dry_run: bool = True,
        promote_threshold: float = 3.0,
        demote_threshold: float = 1.0,
        archive_threshold: float = 0.1,
        half_life_days: int = 30,
        purge_days: int = 365,
        check_conflicts: bool = True,
    ) -> dict:
        """Execute one dreaming pass.

        Args:
            dry_run: If True, only compute what would happen, don't apply changes.
            promote_threshold: Minimum score for promote candidates.
            demote_threshold: Maximum score for demote candidates.
            archive_threshold: Maximum score for archive candidates.
            half_life_days: Half-life for time decay in scoring.
            purge_days: Days after archive before hard delete.
            check_conflicts: Whether to run conflict detection.

        Returns:
            Dict with full dreaming report.
        """
        # Override with config
        promote_threshold = self.scoring_config.get("promote_threshold", promote_threshold)
        demote_threshold = self.scoring_config.get("demote_threshold", demote_threshold)
        archive_threshold = self.scoring_config.get("archive_threshold", archive_threshold)
        half_life_days = self.scoring_config.get("half_life_days", half_life_days)
        purge_days = self.scoring_config.get("purge_days", purge_days)

        now = datetime.now(timezone.utc)

        # ── Step 1: Recalculate all scores ──
        all_entries = self.db.get_all_active()
        scored = compute_scores_for_entries(all_entries, half_life_days=half_life_days, now=now)

        if not dry_run:
            for entry_id, score in scored:
                self.db.update_score(entry_id, score)

        # Build score lookup
        score_map = {eid: score for eid, score in scored}

        # ── Step 2: Identify candidates (per-type thresholds) ──
        # Track entry→type mapping for result breakdown
        entry_type_map = {}

        # Promote: raw entries with score >= type's promote_threshold
        promote_candidates = []
        for e in all_entries:
            entry_type_map[e["id"]] = e.get("memory_type", "knowledge")
            if e["state"] != "raw":
                continue
            if e.get("access_count", 0) < 2:
                continue
            type_cfg = get_type_config(e.get("memory_type", "knowledge"))
            threshold = type_cfg.get("promote_threshold", promote_threshold)
            if score_map.get(e["id"], 0) >= threshold:
                promote_candidates.append(e)

        # Demote: skip non-decaying types (procedural, knowledge never auto-demoted)
        demote_candidates = []
        for e in all_entries:
            if e["state"] not in ("promoted", "hot"):
                continue
            if e.get("is_pinned"):
                continue
            if e.get("access_count", 0) <= 0:
                continue
            type_cfg = get_type_config(e.get("memory_type", "knowledge"))
            if type_cfg.get("half_life_days") is None:
                continue  # Non-decaying types are never auto-demoted
            threshold = type_cfg.get("demote_threshold", demote_threshold)
            if score_map.get(e["id"], 0) < threshold:
                demote_candidates.append(e)

        # Archive: skip non-decaying types
        archive_candidates = []
        for e in all_entries:
            if e["state"] not in ("raw", "promoted", "hot"):
                continue
            if e.get("is_pinned"):
                continue
            if e.get("access_count", 0) <= 0:
                continue
            type_cfg = get_type_config(e.get("memory_type", "knowledge"))
            if type_cfg.get("half_life_days") is None:
                continue  # Non-decaying types are never auto-archived
            threshold = type_cfg.get("archive_threshold", archive_threshold)
            if score_map.get(e["id"], 0) < threshold:
                archive_candidates.append(e)

        # Purge: archived entries older than purge_days
        purge_candidates = self.db.get_purge_candidates(purge_days=purge_days)

        result = {
            "dry_run": dry_run,
            "run_at": now.isoformat(),
            "total_entries": len(all_entries),
            "scores_updated": len(scored) if not dry_run else 0,
            "candidates": {
                "promote": len(promote_candidates),
                "demote": len(demote_candidates),
                "archive": len(archive_candidates),
                "purge": len(purge_candidates),
            },
            "promoted": [],
            "demoted": [],
            "archived": [],
            "purged": [],
            "conflicts_found": 0,
            "conflicts": [],
            "by_type": {},
        }

        if dry_run:
            result["promote_preview"] = [
                {"id": e["id"], "title": e.get("title", ""),
                 "score": score_map.get(e["id"], 0)}
                for e in promote_candidates[:10]
            ]
            result["demote_preview"] = [
                {"id": e["id"], "title": e.get("title", ""),
                 "score": score_map.get(e["id"], 0), "state": e["state"]}
                for e in demote_candidates[:10]
            ]
            result["archive_preview"] = [
                {"id": e["id"], "title": e.get("title", ""),
                 "score": score_map.get(e["id"], 0), "state": e["state"]}
                for e in archive_candidates[:10]
            ]
            return result

        # ── Step 3: Apply transitions ──

        # Purge first (cleanup)
        for entry in purge_candidates[:50]:
            self.db.set_state(entry["id"], "deleted")
            result["purged"].append(entry["id"])

        # Archive
        for entry in archive_candidates[:50]:
            self.db.archive(entry["id"])
            result["archived"].append(entry["id"])

        # Demote
        for entry in demote_candidates[:50]:
            # Demote: hot/promoted → raw (but keep the data)
            self.db.set_state(entry["id"], "raw")
            result["demoted"].append(entry["id"])

        # Promote
        for entry in promote_candidates[:10]:
            self.db.set_state(entry["id"], "promoted")
            result["promoted"].append(entry["id"])

        # Build by_type breakdown
        for mt in ("procedural", "entity", "knowledge"):
            result["by_type"][mt] = {
                "promoted": sum(1 for eid in result["promoted"] if entry_type_map.get(eid) == mt),
                "demoted": sum(1 for eid in result["demoted"] if entry_type_map.get(eid) == mt),
                "archived": sum(1 for eid in result["archived"] if entry_type_map.get(eid) == mt),
                "purged": sum(1 for eid in result["purged"] if entry_type_map.get(eid) == mt),
            }

        # ── Step 4: Conflicts ──
        if check_conflicts:
            conflict_count = self._detect_conflicts()
            result["conflicts_found"] = conflict_count

        # ── Step 5: Rebuild hot layer ──
        if self.hot_layer and (result["promoted"] or result["demoted"] or result["archived"]):
            self.hot_layer.rebuild_all()

        # ── Step 6: Log ──
        self.db.log_dreaming({
            "candidates": len(promote_candidates) + len(demote_candidates),
            "promoted": len(result["promoted"]),
            "demoted": len(result["demoted"]),
            "archived": len(result["archived"]),
            "purged": len(result["purged"]),
            "conflicts_found": result["conflicts_found"],
            "promoted_ids": result["promoted"],
            "demoted_ids": result["demoted"],
            "archived_ids": result["archived"],
            "purged_ids": result["purged"],
        })

        return result

    def promote_single(self, entry_id: int) -> bool:
        """Manually promote an entry to 'promoted' state."""
        entry = self.db.get(entry_id)
        if not entry or entry["state"] == "archived" or entry["state"] == "deleted":
            return False
        self.db.set_state(entry_id, "promoted")
        if self.hot_layer:
            self.hot_layer.rebuild_agent(entry.get("owner_agent", "noor"))
        return True

    def _detect_conflicts(self, recent_days: int = 30) -> int:
        """Detect conflicts in shared/project scope.

        Uses vector similarity (cosine > 0.9) then LLM semantic check.
        Only checks entries updated within recent_days to avoid O(n²) on full corpus.
        """
        if not self.db.has_vector:
            return 0

        # Only check recently updated entries to bound the comparison set
        shared_entries = self.db.fetchall("""
            SELECT * FROM memory_entries
            WHERE scope IN ('shared', 'project')
              AND embedding IS NOT NULL
              AND state != 'deleted'
              AND deleted_at IS NULL
              AND updated_at > datetime('now', ?)
            ORDER BY updated_at DESC
        """, (f"-{recent_days} days",))

        if len(shared_entries) < 2:
            return 0

        from my_agent_memory.db import _enrich_row, blob_to_floats

        entries = [_enrich_row(r) for r in shared_entries]
        conflict_count = 0

        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a, b = entries[i], entries[j]

                # Only check cross-owner or cross-entry pairs
                if a["owner_agent"] == b["owner_agent"]:
                    continue
                if self.db.has_conflict(a["id"], b["id"]):
                    continue

                sim = self._cosine_similarity(
                    blob_to_floats(a.get("embedding", b"")),
                    blob_to_floats(b.get("embedding", b"")),
                )

                if sim > 0.9:
                    # High similarity — verify semantic contradiction with LLM
                    contradiction = self._check_contradiction_llm(a, b, sim)
                    if contradiction is False:
                        # LLM says no contradiction — skip (similar but not conflicting)
                        continue
                    reason = contradiction if isinstance(contradiction, str) else f"High cosine similarity ({sim:.4f}) between entries {a['id']} and {b['id']}"
                    self.db.insert_conflict(a["id"], b["id"], sim, reason)
                    conflict_count += 1

        return conflict_count

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)

    def _check_contradiction_llm(self, entry_a: dict, entry_b: dict, sim: float):
        """Use LLM to check if two similar entries actually contradict each other.

        Returns:
            False — LLM says they do NOT contradict (safe to ignore)
            str — Contradiction reason (record as conflict)
            None — LLM call failed, fall back to cosine-only behavior
        """
        try:
            from my_agent_memory.llm import LLMClient

            prompt = f"""Check if these two memory entries contradict:

A [{entry_a.get('owner_agent', '?')}]: {entry_a.get('title', '?')}
  {entry_a.get('content', '')[:300]}

B [{entry_b.get('owner_agent', '?')}]: {entry_b.get('title', '?')}
  {entry_b.get('content', '')[:300]}

Cosine similarity: {sim:.4f}
Do they state contradictory facts? Reply ONLY "CONTRADICT" or "SIMILAR"."""

            llm = LLMClient()
            response = llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=500,
            )

            if not response:
                return None

            upper = response.strip().upper()
            # Check full response for keywords (reasoning models think first, then answer)
            if "CONTRADICT" in upper:
                return f"LLM verified contradiction (cosine={sim:.4f})"
            elif "SIMILAR" in upper:
                return False  # No contradiction
            else:
                return None  # Ambiguous

        except Exception:
            return None  # LLM failed → fall back
