"""Conflict detection and resolution for multi-agent memory.

Conflicts arise when two agents independently assert contradictory facts
in the shared or project scope. Detection uses vector similarity as a
pre-filter, with optional LLM-based semantic contradiction checking.
"""

import logging
from typing import Optional

from hermes_memory_v2.db import _enrich_row

logger = logging.getLogger("hermes-memory-v2.conflicts")


class ConflictResolver:
    """Manages detection, listing, and resolution of memory conflicts."""

    def __init__(self, db):
        self.db = db

    def get_open_conflicts(self) -> list[dict]:
        """Get all unresolved conflicts."""
        rows = self.db.get_conflicts(status="open")
        result = []
        for row in rows:
            conflict = dict(row)
            conflict["entry_a"] = self.db.get(row["entry_a_id"])
            conflict["entry_b"] = self.db.get(row["entry_b_id"])
            result.append(conflict)
        return result

    def resolve(
        self,
        conflict_id: int,
        strategy: str,
        resolved_by: str = "user",
        merged_content: str = None,
        merged_title: str = None,
    ) -> Optional[dict]:
        """Resolve a conflict.

        Args:
            conflict_id: ID of the conflict to resolve.
            strategy: 'last_write_wins', 'keep_both', 'merge', or 'dismiss'.
            resolved_by: Who resolved it (agent ID or 'user').
            merged_content: New content for 'merge' strategy.
            merged_title: New title for 'merge' strategy.

        Returns:
            The updated conflict record, or None if not found.

        Strategy details:
            last_write_wins: Keep the newer entry, mark old as superseded.
            keep_both: Both entries remain, conflict marked resolved.
            merge: Create a new entry from merged_content, mark both old as superseded.
            dismiss: Mark conflict as dismissed without changes.
        """
        conflict = self.db.fetchone(
            "SELECT * FROM memory_conflicts WHERE id = ? AND status = 'open'",
            (conflict_id,),
        )
        if not conflict:
            return None

        if strategy == "last_write_wins":
            entry_a = self.db.get(conflict["entry_a_id"])
            entry_b = self.db.get(conflict["entry_b_id"])
            if entry_a and entry_b:
                if (entry_a.get("updated_at") or "") >= (entry_b.get("updated_at") or ""):
                    winner, loser = entry_a, entry_b
                else:
                    winner, loser = entry_b, entry_a
                self.db.execute(
                    "UPDATE memory_entries SET superseded_by = ? WHERE id = ?",
                    (winner["id"], loser["id"]),
                )
                self.db.commit()

        elif strategy == "merge":
            if not merged_content:
                return None
            entry_a = self.db.get(conflict["entry_a_id"])
            if entry_a:
                merged = self.db.insert(
                    content=merged_content,
                    title=merged_title or "Merged entry",
                    source="consolidated",
                    owner_agent=entry_a.get("owner_agent", "noor"),
                    scope=entry_a.get("scope", "shared"),
                )
                if merged:
                    # Mark both old entries as superseded
                    for eid in (conflict["entry_a_id"], conflict["entry_b_id"]):
                        self.db.execute(
                            "UPDATE memory_entries SET superseded_by = ? WHERE id = ?",
                            (merged["id"], eid),
                        )
                    self.db.commit()
                    return self.db.resolve_conflict(
                        conflict_id, strategy, resolved_by, merged_into=merged["id"]
                    )

        elif strategy == "keep_both":
            pass  # No changes to entries, just mark conflict resolved

        elif strategy == "dismiss":
            pass  # Mark conflict as dismissed

        return self.db.resolve_conflict(conflict_id, strategy, resolved_by)

    def count_open(self) -> int:
        """Number of unresolved conflicts."""
        row = self.db.fetchone(
            "SELECT COUNT(*) as n FROM memory_conflicts WHERE status = 'open'"
        )
        return row["n"] if row else 0
