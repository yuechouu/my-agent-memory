"""SQLite + FTS5 + sqlite-vec database layer for My Agent Memory.

Multi-agent memory store with:
  - FTS5 full-text search (unicode61 tokenizer, CJK LIKE fallback)
  - sqlite-vec vector similarity search
  - Per-agent namespace isolation
  - Pin mechanism for cold-but-important entries
  - Deduplication via checksum (same owner only)
"""

import hashlib
import json
import logging
import os
import sqlite3
import struct
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from my_agent_memory.schema import SCHEMA, add_column_if_missing

__all__ = [
    "Database", "DEFAULT_DB_PATH",
    "_enrich_row", "_floats_to_blob", "blob_to_floats",
]


def _get_hermes_mem_dir() -> Path:
    hermes_home = os.getenv("HERMES_HOME", "")
    if hermes_home:
        return Path(hermes_home) / "memories"
    return Path.home() / ".hermes" / "memories"


DEFAULT_DB_PATH = _get_hermes_mem_dir() / "memory_v2.db"


class Database:
    """SQLite database with FTS5 and optional vector search."""

    def __init__(self, path: str = "", load_vec: bool = True):
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._vec_loaded = False
        if load_vec:
            self._init_vector()

    def _init_schema(self):
        # Phase 1: Create tables (IF NOT EXISTS is safe for existing DBs)
        # Phase 2: Migrate columns that were added after initial schema
        # Phase 3: Create indexes (which reference all columns)
        from my_agent_memory.schema import SCHEMA_TABLES, SCHEMA_INDEXES
        self.conn.executescript(SCHEMA_TABLES)
        self.conn.commit()
        add_column_if_missing(self.conn, "memory_entries", "validation_status", "TEXT")
        add_column_if_missing(self.conn, "memory_entries", "memory_type", "TEXT NOT NULL DEFAULT 'knowledge'")
        self.conn.executescript(SCHEMA_INDEXES)
        self.conn.commit()

    def _init_vector(self):
        """Load sqlite-vec extension and create vector table if not exists."""
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
                    embedding float[4096]
                )
            """)
            self.conn.commit()
            self._vec_loaded = True
        except Exception:
            self._vec_loaded = False

    @property
    def has_vector(self) -> bool:
        return self._vec_loaded

    def execute(self, sql: str, params=()):
        return self.conn.execute(sql, params)

    def fetchone(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchall()

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ── Entry CRUD ──────────────────────────────────────────

    def insert(self, content: str, title: str = "", tags: list = None,
               source: str = "manual", owner_agent: str = "noor",
               scope: str = "private", project: str = None,
               memory_type: str = "knowledge",
               audit_agent: str = "") -> dict:
        """Insert a new memory entry. Returns the new row as dict.

        Deduplication: same checksum + same owner → update access_count, return existing.
        Different owner + same checksum → allow (cross-agent independent verification).
        """
        tag_str = json.dumps(tags or [], ensure_ascii=False)
        ck = hashlib.md5(content.encode()).hexdigest()[:12]

        # Same-owner dedup check
        existing = self.fetchone(
            """SELECT id FROM memory_entries
               WHERE checksum = ? AND owner_agent = ? AND state != 'archived' AND deleted_at IS NULL""",
            (ck, owner_agent),
        )
        if existing:
            self.execute(
                """UPDATE memory_entries
                   SET access_count = access_count + 1,
                       last_access_ts = datetime('now'),
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (existing["id"],),
            )
            self.commit()
            return _enrich_row(
                self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (existing["id"],))
            )

        cursor = self.execute(
            """INSERT INTO memory_entries
               (content, title, tags, source, checksum, owner_agent, scope, project,
                memory_type, state, last_access_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'raw', datetime('now'))""",
            (content, title, tag_str, source, ck, owner_agent, scope, project, memory_type),
        )
        self.commit()
        result = _enrich_row(
            self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (cursor.lastrowid,))
        )
        if audit_agent:
            self.log_audit(result["id"], "create", audit_agent, new_state="raw",
                          details={"title": title, "scope": scope})
        return result

    def get(self, entry_id: int) -> Optional[dict]:
        """Get a single entry by ID. Updates access_count and last_access_ts."""
        row = self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,))
        if row:
            self.execute(
                """UPDATE memory_entries
                   SET access_count = access_count + 1,
                       last_access_ts = datetime('now')
                   WHERE id = ?""",
                (entry_id,),
            )
            self.commit()
            return _enrich_row(row)
        return None

    def update(self, entry_id: int, content: str = None, title: str = None,
               tags: list = None, scope: str = None, project: str = None,
               memory_type: str = None) -> Optional[dict]:
        """Update an entry. Only provided fields are changed."""
        row = self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,))
        if not row:
            return None

        new_content = content if content is not None else row["content"]
        new_title = title if title is not None else row["title"]
        new_tags = json.dumps(tags, ensure_ascii=False) if tags is not None else row["tags"]
        new_scope = scope if scope is not None else row["scope"]
        new_project = project if project is not None else row["project"]
        new_type = memory_type if memory_type is not None else row["memory_type"]
        ck = hashlib.md5(new_content.encode()).hexdigest()[:12]

        self.execute(
            """UPDATE memory_entries
               SET content = ?, title = ?, tags = ?, checksum = ?,
                   scope = ?, project = ?, memory_type = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (new_content, new_title, new_tags, ck, new_scope, new_project, new_type, entry_id),
        )
        self.commit()
        return _enrich_row(
            self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,))
        )

    def archive(self, entry_id: int, audit_agent: str = "") -> Optional[dict]:
        """Soft-delete: set state='archived', archived_at=now."""
        row = self.fetchone("SELECT id FROM memory_entries WHERE id = ? AND deleted_at IS NULL", (entry_id,))
        if not row:
            return None
        self.execute(
            """UPDATE memory_entries
               SET state = 'archived', archived_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""",
            (entry_id,),
        )
        self.commit()
        result = _enrich_row(self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)))
        if audit_agent:
            self.log_audit(entry_id, "archive", audit_agent, new_state="archived")
        return result

    def delete(self, entry_id: int, audit_agent: str = "") -> bool:
        """Hard-delete: set state='deleted', deleted_at=now. Only for archived entries."""
        row = self.fetchone(
            "SELECT id, state FROM memory_entries WHERE id = ? AND deleted_at IS NULL",
            (entry_id,),
        )
        if not row or row["state"] != "archived":
            return False
        self.execute(
            """UPDATE memory_entries
               SET state = 'deleted', deleted_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""",
            (entry_id,),
        )
        self.commit()
        if audit_agent:
            self.log_audit(entry_id, "delete", audit_agent, new_state="deleted")
        return True

    def set_state(self, entry_id: int, state: str, audit_agent: str = "") -> Optional[dict]:
        """Directly set lifecycle state (used by dreaming engine)."""
        valid_states = ("raw", "promoted", "hot", "archived", "deleted")
        if state not in valid_states:
            raise ValueError(f"Invalid state: {state}")
        self.execute(
            """UPDATE memory_entries SET state = ?, updated_at = datetime('now') WHERE id = ?""",
            (state, entry_id),
        )
        self.commit()
        if audit_agent:
            self.log_audit(entry_id, state, audit_agent, new_state=state)
        return _enrich_row(self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)))

    # ── Pin ─────────────────────────────────────────────────

    def pin(self, entry_id: int, audit_agent: str = "") -> Optional[dict]:
        row = self.fetchone("SELECT id FROM memory_entries WHERE id = ? AND deleted_at IS NULL", (entry_id,))
        if not row:
            return None
        self.execute("UPDATE memory_entries SET is_pinned = 1, updated_at = datetime('now') WHERE id = ?", (entry_id,))
        self.commit()
        if audit_agent:
            self.log_audit(entry_id, "pin", audit_agent)
        return _enrich_row(self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)))

    def unpin(self, entry_id: int, audit_agent: str = "") -> Optional[dict]:
        row = self.fetchone("SELECT id FROM memory_entries WHERE id = ? AND deleted_at IS NULL", (entry_id,))
        if not row:
            return None
        self.execute("UPDATE memory_entries SET is_pinned = 0, updated_at = datetime('now') WHERE id = ?", (entry_id,))
        self.commit()
        if audit_agent:
            self.log_audit(entry_id, "unpin", audit_agent)
        return _enrich_row(self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)))

    # ── Scope ────────────────────────────────────────────────

    def share(self, entry_id: int, audit_agent: str = "") -> Optional[dict]:
        row = self.fetchone("SELECT id FROM memory_entries WHERE id = ? AND deleted_at IS NULL", (entry_id,))
        if not row:
            return None
        self.execute("UPDATE memory_entries SET scope = 'shared', updated_at = datetime('now') WHERE id = ?", (entry_id,))
        self.commit()
        if audit_agent:
            self.log_audit(entry_id, "share", audit_agent)
        return _enrich_row(self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)))

    def unshare(self, entry_id: int, audit_agent: str = "") -> Optional[dict]:
        row = self.fetchone("SELECT id FROM memory_entries WHERE id = ? AND deleted_at IS NULL", (entry_id,))
        if not row:
            return None
        self.execute("UPDATE memory_entries SET scope = 'private', updated_at = datetime('now') WHERE id = ?", (entry_id,))
        self.commit()
        if audit_agent:
            self.log_audit(entry_id, "unshare", audit_agent)
        return _enrich_row(self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)))

    # ── Embedding ────────────────────────────────────────────

    def set_embedding(self, entry_id: int, embedding: bytes, model: str):
        """Store the embedding vector for an entry."""
        self.execute(
            "UPDATE memory_entries SET embedding = ?, embedding_model = ? WHERE id = ?",
            (embedding, model, entry_id),
        )
        self.commit()

    def get_entries_without_embedding(self, limit: int = 50) -> list:
        """Get entries that need embedding generation."""
        rows = self.fetchall(
            """SELECT * FROM memory_entries
               WHERE embedding IS NULL AND deleted_at IS NULL AND content != ''
               ORDER BY created_at ASC LIMIT ?""",
            (limit,),
        )
        return [_enrich_row(r) for r in rows]

    # ── Validation ───────────────────────────────────────────

    def set_validation_status(self, entry_id: int, status: str):
        """Set the async validation status for an entry (clean/flagged:reason/error)."""
        self.execute(
            "UPDATE memory_entries SET validation_status = ? WHERE id = ?",
            (status, entry_id),
        )
        self.commit()

    def get_unvalidated(self, limit: int = 20) -> list:
        """Get entries that haven't been async-validated yet."""
        rows = self.fetchall(
            """SELECT id, title, content FROM memory_entries
               WHERE validation_status IS NULL AND deleted_at IS NULL AND content != ''
               ORDER BY created_at ASC LIMIT ?""",
            (limit,),
        )
        return [_enrich_row(r) for r in rows]

    def get_flagged(self) -> list:
        """Get entries flagged by async validation."""
        rows = self.fetchall(
            """SELECT * FROM memory_entries
               WHERE validation_status LIKE 'flagged:%' AND deleted_at IS NULL
               ORDER BY updated_at DESC"""
        )
        return [_enrich_row(r) for r in rows]

    # ── Search ───────────────────────────────────────────────

    def _build_visibility_filter(self, agent_id: str, scope: str = None, project: str = None):
        """Build WHERE clause for agent visibility rules."""
        if agent_id == "*":
            return "1=1", []

        if scope == "shared":
            return "e.scope = 'shared'", []
        if scope == "project" and project:
            return "e.scope = 'project' AND e.project = ?", [project]

        conditions = [
            "(e.owner_agent = ? AND e.scope = 'private')",
            "e.scope = 'shared'",
        ]
        params = [agent_id]
        if project:
            conditions.append("(e.scope = 'project' AND e.project = ?)")
            params.append(project)
        else:
            conditions.append("e.scope = 'project'")

        return f"({' OR '.join(conditions)})", params

    def search(self, query: str, agent_id: str = "*", limit: int = 10, offset: int = 0,
               tags: list = None, scope: str = None, project: str = None,
               memory_type: str = None) -> list:
        """FTS5 full-text search with CJK LIKE fallback and visibility filtering."""
        import re

        vis_filter, vis_params = self._build_visibility_filter(agent_id, scope, project)
        has_cjk = bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', query))

        where = ["memory_fts MATCH ?", vis_filter, "e.deleted_at IS NULL", "e.state != 'archived'"]
        params = [query] + vis_params

        if memory_type:
            where.append("e.memory_type = ?")
            params.append(memory_type)

        if tags:
            tag_cond = " OR ".join(["e.tags LIKE ?" for _ in tags])
            where.append(f"({tag_cond})")
            params.extend([f'%"\\{t}"%' for t in tags])

        sql = f"""
            SELECT e.*, rank AS score
            FROM memory_fts f
            JOIN memory_entries e ON f.rowid = e.id
            WHERE {' AND '.join(where)}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = self.fetchall(sql, tuple(params))

        # CJK fallback
        if has_cjk and not rows:
            like_where = ["e.content LIKE ?", vis_filter, "e.deleted_at IS NULL", "e.state != 'archived'"]
            like_params = [f"%{query}%"] + vis_params
            like_sql = f"""
                SELECT e.*, 0.5 AS score
                FROM memory_entries e
                WHERE {' AND '.join(like_where)}
                ORDER BY e.access_count DESC, e.updated_at DESC
                LIMIT ? OFFSET ?
            """
            like_params.extend([limit, offset])
            rows = self.fetchall(like_sql, tuple(like_params))

        # Touch access counters
        for r in rows:
            self.execute(
                """UPDATE memory_entries
                   SET access_count = access_count + 1,
                       last_access_ts = datetime('now')
                   WHERE id = ?""",
                (r["id"],),
            )
        self.commit()

        return [_enrich_row(r) for r in rows]

    def search_raw(self, keyword: str, limit: int = 20) -> list:
        """Simple LIKE search for debugging."""
        rows = self.fetchall(
            """SELECT * FROM memory_entries
               WHERE (content LIKE ? OR title LIKE ? OR tags LIKE ?)
                 AND deleted_at IS NULL
               ORDER BY updated_at DESC LIMIT ?""",
            (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit),
        )
        return [_enrich_row(r) for r in rows]

    # ── Vector Search ────────────────────────────────────────

    def vector_search(self, query_vector: list[float], candidate_ids: list[int] = None,
                      limit: int = 20) -> list[tuple[int, float]]:
        """Search by vector similarity. Returns list of (entry_id, distance)."""
        if not self._vec_loaded:
            return []

        # Build blob from float list
        struct_blob = _floats_to_blob(query_vector)

        if candidate_ids:
            # Filtered search within candidates
            placeholders = ",".join(["?" for _ in candidate_ids])
            rows = self.fetchall(f"""
                SELECT v.rowid AS id, v.distance
                FROM memory_vec v
                WHERE v.embedding MATCH ? AND v.rowid IN ({placeholders})
                ORDER BY v.distance
                LIMIT ?
            """, [struct_blob] + candidate_ids + [limit])
        else:
            rows = self.fetchall("""
                SELECT v.rowid AS id, v.distance
                FROM memory_vec v
                WHERE v.embedding MATCH ?
                ORDER BY v.distance
                LIMIT ?
            """, [struct_blob, limit])

        return [(r["id"], r["distance"]) for r in rows]

    def index_vector(self, entry_id: int, embedding: list[float]):
        """Insert or update vector index for an entry."""
        if not self._vec_loaded:
            return
        struct_blob = _floats_to_blob(embedding)
        # Delete existing
        self.execute("DELETE FROM memory_vec WHERE rowid = ?", (entry_id,))
        # Insert new
        self.execute("INSERT INTO memory_vec (rowid, embedding) VALUES (?, ?)", (entry_id, struct_blob))
        self.commit()

    def remove_vector(self, entry_id: int):
        """Remove vector index for an entry."""
        if not self._vec_loaded:
            return
        self.execute("DELETE FROM memory_vec WHERE rowid = ?", (entry_id,))
        self.commit()

    # ── Dreaming Candidates ──────────────────────────────────

    def get_dreaming_candidates(self, min_score: float = 3.0, min_access: int = 2) -> list:
        """Get entries that are candidates for promotion (score >= threshold)."""
        rows = self.fetchall("""
            SELECT * FROM memory_entries
            WHERE state = 'raw'
              AND deleted_at IS NULL
              AND state != 'archived'
              AND score >= ?
              AND access_count >= ?
            ORDER BY score DESC
            LIMIT 50
        """, (min_score, min_access))
        return [_enrich_row(r) for r in rows]

    def get_demote_candidates(self, max_score: float = 1.0) -> list:
        """Get entries that should be demoted (score below threshold, not pinned)."""
        rows = self.fetchall("""
            SELECT * FROM memory_entries
            WHERE state IN ('promoted', 'hot')
              AND is_pinned = 0
              AND deleted_at IS NULL
              AND score < ?
            ORDER BY score ASC
            LIMIT 50
        """, (max_score,))
        return [_enrich_row(r) for r in rows]

    def get_archive_candidates(self, max_score: float = 0.1) -> list:
        """Get entries that should be archived (very low score, not pinned)."""
        rows = self.fetchall("""
            SELECT * FROM memory_entries
            WHERE state IN ('raw', 'promoted', 'hot')
              AND is_pinned = 0
              AND deleted_at IS NULL
              AND state != 'archived'
              AND score < ?
            ORDER BY score ASC
            LIMIT 50
        """, (max_score,))
        return [_enrich_row(r) for r in rows]

    def get_purge_candidates(self, purge_days: int = 365) -> list:
        """Get archived entries older than N days (candidates for hard delete)."""
        rows = self.fetchall("""
            SELECT * FROM memory_entries
            WHERE state = 'archived'
              AND deleted_at IS NULL
              AND julianday('now') - julianday(archived_at) > ?
            LIMIT 50
        """, (purge_days,))
        return [_enrich_row(r) for r in rows]

    def get_all_active(self) -> list:
        """Get all non-deleted, non-archived entries (for scoring recalculation)."""
        rows = self.fetchall("""
            SELECT * FROM memory_entries
            WHERE state != 'deleted' AND state != 'archived'
        """)
        return [_enrich_row(r) for r in rows]

    def update_score(self, entry_id: int, score: float):
        """Update the score field for an entry."""
        self.execute("UPDATE memory_entries SET score = ? WHERE id = ?", (score, entry_id))
        self.commit()

    # ── Hot Layer ────────────────────────────────────────────

    def get_hot_entries(self, agent_id: str, include_shared: bool = True,
                        memory_type: str = None) -> list:
        """Get entries that should appear in the hot layer (promoted + hot)."""
        conditions = [
            "(e.state IN ('promoted', 'hot'))",
            "e.deleted_at IS NULL",
        ]
        params = []

        if include_shared:
            conditions.append(f"(e.owner_agent = ? OR e.scope = 'shared')")
        else:
            conditions.append(f"(e.owner_agent = ? AND e.scope != 'shared')")
        params.append(agent_id)

        if memory_type:
            conditions.append("e.memory_type = ?")
            params.append(memory_type)

        rows = self.fetchall(f"""
            SELECT * FROM memory_entries e
            WHERE {' AND '.join(conditions)}
            ORDER BY e.is_pinned DESC, e.score DESC, e.updated_at DESC
        """, tuple(params))
        return [_enrich_row(r) for r in rows]

    # ── Conflicts ────────────────────────────────────────────

    def insert_conflict(self, entry_a_id: int, entry_b_id: int,
                        similarity: float, reason: str = "") -> dict:
        """Record a detected conflict between two entries."""
        cursor = self.execute(
            """INSERT INTO memory_conflicts (entry_a_id, entry_b_id, similarity, reason)
               VALUES (?, ?, ?, ?)""",
            (entry_a_id, entry_b_id, similarity, reason),
        )
        self.commit()
        return dict(self.fetchone("SELECT * FROM memory_conflicts WHERE id = ?", (cursor.lastrowid,)))

    def get_conflicts(self, status: str = "open") -> list:
        """Get conflicts by status."""
        rows = self.fetchall(
            "SELECT * FROM memory_conflicts WHERE status = ? ORDER BY created_at DESC",
            (status,),
        )
        return [dict(r) for r in rows]

    def resolve_conflict(self, conflict_id: int, strategy: str,
                         resolved_by: str = "user", merged_into: int = None) -> Optional[dict]:
        """Resolve a conflict."""
        status_map = {
            "last_write_wins": "resolved_last_write_wins",
            "keep_both": "resolved_keep_both",
            "merge": "resolved_merged",
            "dismiss": "dismissed",
        }
        new_status = status_map.get(strategy, "dismissed")
        self.execute(
            """UPDATE memory_conflicts
               SET status = ?, resolved_by = ?, resolved_at = datetime('now'),
                   merged_into = ?
               WHERE id = ?""",
            (new_status, resolved_by, merged_into, conflict_id),
        )
        self.commit()
        return dict(self.fetchone("SELECT * FROM memory_conflicts WHERE id = ?", (conflict_id,)))

    def has_conflict(self, entry_a_id: int, entry_b_id: int) -> bool:
        """Check if a conflict already exists between these two entries."""
        row = self.fetchone(
            """SELECT id FROM memory_conflicts
               WHERE status = 'open'
                 AND ((entry_a_id = ? AND entry_b_id = ?)
                   OR (entry_a_id = ? AND entry_b_id = ?))""",
            (entry_a_id, entry_b_id, entry_b_id, entry_a_id),
        )
        return row is not None

    # ── Dreaming Log ─────────────────────────────────────────

    def log_dreaming(self, details: dict):
        """Record a dreaming run."""
        self.execute(
            """INSERT INTO dreaming_log
               (candidates, promoted, demoted, archived, purged, conflicts_found, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                details.get("candidates", 0),
                details.get("promoted", 0),
                details.get("demoted", 0),
                details.get("archived", 0),
                details.get("purged", 0),
                details.get("conflicts_found", 0),
                json.dumps(details, ensure_ascii=False),
            ),
        )
        self.commit()

    def get_dreaming_log(self, limit: int = 20) -> list[dict]:
        """Get recent dreaming run records."""
        rows = self.fetchall(
            "SELECT * FROM dreaming_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["details"] = {}
            result.append(d)
        return result

    # ── Audit Log ────────────────────────────────────────────

    def log_audit(self, entry_id: int, action: str, agent_id: str = "",
                  old_state: str = "", new_state: str = "", details: dict = None):
        """Record an audit trail entry for a write operation."""
        self.execute(
            """INSERT INTO memory_audit_log (entry_id, action, agent_id, old_state, new_state, details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entry_id, action, agent_id, old_state, new_state,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
        self.commit()

    def get_audit_log(self, entry_id: int = None, action: str = None,
                      agent_id: str = None, limit: int = 50) -> list[dict]:
        """Query audit log with optional filters."""
        where = ["1=1"]
        params = []
        if entry_id:
            where.append("entry_id = ?")
            params.append(entry_id)
        if action:
            where.append("action = ?")
            params.append(action)
        if agent_id:
            where.append("agent_id = ?")
            params.append(agent_id)

        rows = self.fetchall(
            f"SELECT * FROM memory_audit_log WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
            tuple(params) + (limit,),
        )
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["details"] = {}
            result.append(d)
        return result

    # ── Stats ────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return memory statistics."""
        total = self.fetchone("SELECT COUNT(*) as n FROM memory_entries WHERE deleted_at IS NULL")["n"]
        by_state = {}
        for state in ("raw", "promoted", "hot", "archived", "deleted"):
            n = self.fetchone(
                f"SELECT COUNT(*) as n FROM memory_entries WHERE state = ? AND deleted_at IS NULL",
                (state,),
            )["n"]
            by_state[state] = n
        by_scope = {}
        for scope in ("private", "shared", "project"):
            n = self.fetchone(
                "SELECT COUNT(*) as n FROM memory_entries WHERE scope = ? AND deleted_at IS NULL",
                (scope,),
            )["n"]
            by_scope[scope] = n
        by_agent = {}
        for row in self.fetchall(
            "SELECT owner_agent, COUNT(*) as n FROM memory_entries WHERE deleted_at IS NULL GROUP BY owner_agent"
        ):
            by_agent[row["owner_agent"]] = row["n"]
        by_type = {}
        for mt in ("procedural", "entity", "knowledge"):
            n = self.fetchone(
                "SELECT COUNT(*) as n FROM memory_entries WHERE memory_type = ? AND deleted_at IS NULL",
                (mt,),
            )["n"]
            by_type[mt] = n
        pinned = self.fetchone(
            "SELECT COUNT(*) as n FROM memory_entries WHERE is_pinned = 1 AND deleted_at IS NULL"
        )["n"]
        open_conflicts = self.fetchone(
            "SELECT COUNT(*) as n FROM memory_conflicts WHERE status = 'open'"
        )["n"]
        last_dreaming = self.fetchone("SELECT run_at FROM dreaming_log ORDER BY id DESC LIMIT 1")

        return {
            "total": total,
            "promoted": by_state.get("promoted", 0) + by_state.get("hot", 0),
            "raw": by_state.get("raw", 0),
            "archived": by_state.get("archived", 0),
            "by_state": by_state,
            "by_scope": by_scope,
            "by_type": by_type,
            "by_agent": by_agent,
            "pinned": pinned,
            "open_conflicts": open_conflicts,
            "last_dreaming": last_dreaming["run_at"] if last_dreaming else None,
            "db_path": str(self.path),
        }

    def get_tag_frequencies(self, limit: int = 50) -> list:
        """Get tag frequency table across all active entries.

        Returns: [{"tag": "python", "count": 12}, ...] sorted by count desc.
        """
        rows = self.fetchall(
            "SELECT tags FROM memory_entries WHERE deleted_at IS NULL AND state != 'archived'"
        )
        from collections import Counter
        counter = Counter()
        for row in rows:
            try:
                tags = json.loads(row["tags"])
                counter.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass
        return [{"tag": t, "count": c} for t, c in counter.most_common(limit)]

    def list_entries(self, agent_id: str = None, scope: str = None, state: str = None,
                     memory_type: str = None,
                     page: int = 1, limit: int = 20, query: str = None,
                     sort_by: str = "", sort_order: str = "desc") -> dict:
        """Paginated entry listing with filters and sort (for dashboard API)."""
        where = ["e.deleted_at IS NULL"]
        params = []

        if agent_id and agent_id != "*":
            where.append("e.owner_agent = ?")
            params.append(agent_id)
        if scope:
            where.append("e.scope = ?")
            params.append(scope)
        if state:
            where.append("e.state = ?")
            params.append(state)
        if memory_type:
            where.append("e.memory_type = ?")
            params.append(memory_type)
        if query:
            where.append("(e.content LIKE ? OR e.title LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])

        where_clause = " AND ".join(where)

        # Count
        total_row = self.fetchone(f"SELECT COUNT(*) as n FROM memory_entries e WHERE {where_clause}", tuple(params))
        total = total_row["n"] if total_row else 0

        # Sort
        order = "e.is_pinned DESC, e.score DESC, e.updated_at DESC"
        allowed_sorts = {
            "score": "e.score", "access_count": "e.access_count",
            "updated_at": "e.updated_at", "created_at": "e.created_at",
            "title": "e.title",
        }
        if sort_by in allowed_sorts:
            col = allowed_sorts[sort_by]
            dir_sql = "ASC" if sort_order == "asc" else "DESC"
            order = f"e.is_pinned DESC, {col} {dir_sql}"

        # Page
        offset = (page - 1) * limit
        rows = self.fetchall(
            f"""SELECT * FROM memory_entries e
                WHERE {where_clause}
                ORDER BY {order}
                LIMIT ? OFFSET ?""",
            tuple(params) + (limit, offset),
        )

        return {
            "entries": [_enrich_row(r) for r in rows],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit if total > 0 else 1,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }

    # ── RAG Operations ──────────────────────────────────────────

    def upsert_rag_document(self, doc_id: str, source: str, title: str = None,
                            domain: str = None, tags: list = None,
                            content_hash: str = "", chunk_count: int = 0,
                            metadata: dict = None) -> dict:
        """Insert or update a RAG document."""
        tag_str = json.dumps(tags or [], ensure_ascii=False)
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)

        self.execute(
            """INSERT OR REPLACE INTO rag_documents
               (id, source, title, domain, tags, content_hash, chunk_count, metadata, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (doc_id, source, title, domain, tag_str, content_hash, chunk_count, meta_str),
        )
        self.commit()
        return self.get_rag_document(doc_id)

    def get_rag_document(self, doc_id: str) -> Optional[dict]:
        """Get RAG document by ID."""
        row = self.fetchone("SELECT * FROM rag_documents WHERE id = ?", (doc_id,))
        if row:
            d = dict(row)
            try:
                d["tags"] = json.loads(d.get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
            try:
                d["metadata"] = json.loads(d.get("metadata", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["metadata"] = {}
            return d
        return None

    def list_rag_documents(self, domain: str = None, limit: int = 50) -> list:
        """List RAG documents, optionally filtered by domain."""
        if domain:
            rows = self.fetchall(
                "SELECT * FROM rag_documents WHERE domain = ? ORDER BY ingested_at DESC LIMIT ?",
                (domain, limit),
            )
        else:
            rows = self.fetchall(
                "SELECT * FROM rag_documents ORDER BY ingested_at DESC LIMIT ?",
                (limit,),
            )
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["tags"] = json.loads(d.get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
            result.append(d)
        return result

    def delete_rag_document(self, doc_id: str) -> bool:
        """Delete a RAG document and all its chunks."""
        row = self.fetchone("SELECT id FROM rag_documents WHERE id = ?", (doc_id,))
        if not row:
            return False
        self.execute("DELETE FROM rag_documents WHERE id = ?", (doc_id,))
        self.commit()
        return True

    def upsert_rag_chunk(self, chunk_id: str, document_id: str, chunk_index: int,
                         content: str, heading: str = None,
                         start_line: int = None, end_line: int = None):
        """Insert or update a RAG chunk."""
        self.execute(
            """INSERT OR REPLACE INTO rag_chunks
               (id, document_id, chunk_index, content, heading, start_line, end_line)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (chunk_id, document_id, chunk_index, content, heading, start_line, end_line),
        )
        # Don't commit here - batch commit after all chunks

    def update_rag_chunk_embedding(self, chunk_id: str, embedding: list[float]):
        """Update the embedding vector for a RAG chunk."""
        blob = _floats_to_blob(embedding)
        self.execute(
            "UPDATE rag_chunks SET embedding = ? WHERE id = ?",
            (blob, chunk_id),
        )
        # Also index in vec table if available
        if self._vec_loaded:
            try:
                self.execute(
                    "INSERT OR REPLACE INTO rag_chunks_vec (id, embedding) VALUES (?, ?)",
                    (chunk_id, blob),
                )
            except Exception:
                pass  # Vec table might not exist yet
        self.commit()

    def search_rag_fts(self, query: str, limit: int = 10) -> list[dict]:
        """FTS5 search on RAG chunks."""
        try:
            rows = self.fetchall(
                """SELECT c.*, d.source, d.title, d.domain
                   FROM rag_chunks_fts fts
                   JOIN rag_chunks c ON c.rowid = fts.rowid
                   JOIN rag_documents d ON d.id = c.document_id
                   WHERE rag_chunks_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            )
            return [dict(r) for r in rows]
        except Exception:
            # Fallback to LIKE search
            rows = self.fetchall(
                """SELECT c.*, d.source, d.title, d.domain
                   FROM rag_chunks c
                   JOIN rag_documents d ON d.id = c.document_id
                   WHERE c.content LIKE ? OR c.heading LIKE ?
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            )
            return [dict(r) for r in rows]

    def search_rag_vec(self, query_vector: list[float], limit: int = 10) -> list[dict]:
        """Vector search on RAG chunks."""
        if not self._vec_loaded:
            return []

        try:
            blob = _floats_to_blob(query_vector)
            rows = self.fetchall(
                """SELECT c.*, d.source, d.title, d.domain
                   FROM rag_chunks_vec v
                   JOIN rag_chunks c ON c.id = v.id
                   JOIN rag_documents d ON d.id = c.document_id
                   WHERE v.embedding MATCH ?
                   ORDER BY distance
                   LIMIT ?""",
                (blob, limit),
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"RAG vector search failed: {e}")
            return []

    def commit_rag_chunks(self):
        """Commit all pending RAG chunk inserts."""
        self.commit()

    # ── Learning Memory Operations ──────────────────────────────────────────

    def get_learned_candidates_for_promotion(self, min_access: int = 3) -> list:
        """Get learned memories that are candidates for promotion."""
        rows = self.fetchall(
            """SELECT id, memory_type, score, access_count
               FROM memory_entries
               WHERE memory_type LIKE 'learned-%'
                 AND state = 'raw'
                 AND access_count >= ?
                 AND deleted_at IS NULL
               ORDER BY score DESC""",
            (min_access,),
        )
        return [dict(r) for r in rows]

    def promote_memory(self, entry_id: int, new_type: str, audit_agent: str = "") -> Optional[dict]:
        """Promote a memory to a new type."""
        row = self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,))
        if not row:
            return None

        old_type = row["memory_type"]
        self.execute(
            """UPDATE memory_entries
               SET memory_type = ?, state = 'promoted', promoted_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""",
            (new_type, entry_id),
        )
        self.commit()

        if audit_agent:
            self.log_audit(entry_id, "promote", audit_agent,
                          old_state=old_type, new_state=new_type)

        return _enrich_row(
            self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,))
        )

    # ── Maintenance ──────────────────────────────────────────

    def rebuild_fts(self):
        """Rebuild FTS5 index."""
        self.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
        self.commit()


def _enrich_row(row) -> dict:
    """Convert row to dict with parsed JSON fields."""
    d = dict(row)
    for field in ("tags", "consolidated_from"):
        try:
            d[field] = json.loads(d.get(field, "[]"))
        except (json.JSONDecodeError, TypeError):
            d[field] = []
    d["is_pinned"] = bool(d.get("is_pinned", 0))
    return d


def _floats_to_blob(floats: list[float]) -> bytes:
    """Pack float list into binary blob (little-endian, single precision)."""
    return struct.pack(f"<{len(floats)}f", *floats)


def blob_to_floats(blob: bytes) -> list[float]:
    """Unpack binary blob into float list."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))
