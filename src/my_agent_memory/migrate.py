"""v1 → v2 migration — incremental, non-destructive.

Strategy:
  - Read v1 memory.db → insert into memory_v2.db with owner_agent, scope=private
  - Move v1 hot layer files into per-agent directories
  - v1 original files are never modified
"""

import json
import os
import shutil
from pathlib import Path
from datetime import datetime


def migrate_from_v1(
    v1_db_path: str,
    v1_hot_dir: str,
    agent_id: str = "noor",
    v2_db_path: str = "",
    dry_run: bool = True,
) -> dict:
    """Migrate v1 data to v2 format.

    Args:
        v1_db_path: Path to v1 memory.db.
        v1_hot_dir: Path to v1 hot layer directory (memories/).
        agent_id: Owner agent for migrated entries.
        v2_db_path: Path for new v2 database. Default: same dir as v1, memory_v2.db.
        dry_run: If True, preview only, don't write.

    Returns:
        Report dict with migration details.
    """
    v1_path = Path(v1_db_path)
    hot_path = Path(v1_hot_dir)

    if not v1_path.exists():
        return {"error": f"v1 database not found: {v1_path}"}
    if not hot_path.is_dir():
        return {"error": f"v1 hot directory not found: {hot_path}"}

    if not v2_db_path:
        v2_db_path = str(v1_path.parent / "memory_v2.db")

    import sqlite3
    from my_agent_memory.db import Database

    report = {
        "dry_run": dry_run,
        "v1_db": str(v1_path),
        "v1_hot": str(hot_path),
        "agent_id": agent_id,
        "v2_db": str(v2_db_path),
        "entries_migrated": 0,
        "entries_skipped": 0,
        "hot_files_moved": [],
        "timestamp": datetime.now().isoformat(),
    }

    # ── Phase 1: DB migration ──
    v1_conn = sqlite3.connect(str(v1_path))
    v1_conn.row_factory = sqlite3.Row

    # Count v1 entries
    v1_count = v1_conn.execute("SELECT COUNT(*) as n FROM memory_entries").fetchone()["n"]
    report["v1_total_entries"] = v1_count

    if not dry_run:
        v2_db = Database(v2_db_path)

        rows = v1_conn.execute("SELECT * FROM memory_entries").fetchall()
        for row in rows:
            try:
                tags_str = row["tags"] if row["tags"] else "[]"
                try:
                    tags = json.loads(tags_str)
                except (json.JSONDecodeError, TypeError):
                    tags = [tags_str] if tags_str else []

                # Determine initial state
                state = "raw"
                if row["promoted"]:
                    state = "promoted"

                v2_db.execute(
                    """INSERT INTO memory_entries
                       (content, title, tags, source, checksum,
                        owner_agent, scope, state, is_pinned,
                        access_count, created_at, updated_at,
                        promoted_at, archived_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'private', ?, 0, ?, ?, ?, ?, ?)""",
                    (
                        row["content"],
                        row["title"] or "",
                        json.dumps(tags, ensure_ascii=False),
                        row["source"] or "manual",
                        row["checksum"] or "",
                        agent_id,
                        state,
                        row["access_count"] or 0,
                        row["created_at"] or datetime.now().isoformat(),
                        row["updated_at"] or datetime.now().isoformat(),
                        row["promoted_at"] if row["promoted"] else None,
                        row["archived_at"] if row["archived"] else None,
                    ),
                )
                report["entries_migrated"] += 1

                if row["archived"]:
                    v2_db.execute(
                        "UPDATE memory_entries SET state = 'archived' WHERE checksum = ? AND owner_agent = ?",
                        (row["checksum"] or "", agent_id),
                    )

            except Exception as e:
                report["entries_skipped"] += 1
                if "errors" not in report:
                    report["errors"] = []
                report["errors"].append(f"Failed to migrate entry {row['id']}: {e}")

        v2_db.commit()
        v2_db.close()

    v1_conn.close()

    # ── Phase 2: Hot layer migration ──
    # Move MEMORY.md and topic files into per-agent directory
    target_dir = hot_path / agent_id
    shared_dir = hot_path / "shared"

    md_files = list(hot_path.glob("*.md"))
    for f in md_files:
        if f.name in ("MEMORY.md", "USER.md"):
            dest = target_dir / f.name
        else:
            dest = target_dir / f.name

        report["hot_files_moved"].append({
            "from": str(f),
            "to": str(dest),
        })

        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            if f.exists():
                shutil.copy2(str(f), str(dest))  # copy, don't move (safe)

    # Create shared directory
    if not dry_run:
        shared_dir.mkdir(parents=True, exist_ok=True)
        # Create empty shared MEMORY.md if not exists
        shared_md = shared_dir / "MEMORY.md"
        if not shared_md.exists():
            shared_md.write_text(
                "# Shared Memory\n*0 entries · created by migration*\n",
                encoding="utf-8",
            )

    # ── Backup v1 DB ──
    v1_backup = v1_path.with_name("memory_v1_backup.db")
    if not dry_run and not v1_backup.exists():
        shutil.copy2(str(v1_path), str(v1_backup))
        report["v1_backup"] = str(v1_backup)

    return report
