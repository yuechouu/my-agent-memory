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
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


def _get_hermes_mem_dir() -> Path:
    hermes_home = os.getenv("HERMES_HOME", "")
    if hermes_home:
        return Path(hermes_home) / "memories"
    return Path.home() / ".hermes" / "memories"


DEFAULT_DB_PATH = _get_hermes_mem_dir() / "memory_v2.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    title           TEXT DEFAULT '',
    tags            TEXT DEFAULT '[]',
    source          TEXT DEFAULT 'manual',
    checksum        TEXT,

    -- Ownership & visibility
    owner_agent     TEXT NOT NULL DEFAULT 'noor',
    scope           TEXT NOT NULL DEFAULT 'private',  -- private / shared / project
    project         TEXT,

    -- Lifecycle
    state           TEXT NOT NULL DEFAULT 'raw',   -- raw / promoted / hot / archived / deleted
    is_pinned       INTEGER DEFAULT 0,             -- 1 = dreaming skips demote/archive
    promoted_at     TEXT,
    archived_at     TEXT,
    deleted_at      TEXT,

    -- Scoring (access_count + last_access_ts only; full audit in dreaming_log)
    access_count    INTEGER DEFAULT 0,
    last_access_ts  TEXT,
    score           REAL DEFAULT 0.0,

    -- Vector (4096-dim float32 blob from Qwen3-Embedding-8B)
    embedding       BLOB,
    embedding_model TEXT,

    -- Relations
    consolidated_from TEXT DEFAULT '[]',
    superseded_by   INTEGER,

    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- FTS5 full-text index (auto-synced via triggers)
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    title,
    content,
    tags,
    tokenize='unicode61',
    content='memory_entries',
    content_rowid='id'
);

-- FTS sync triggers
CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memory_entries BEGIN
    INSERT INTO memory_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
    INSERT INTO memory_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;

-- Conflict detection table
CREATE TABLE IF NOT EXISTS memory_conflicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_a_id      INTEGER NOT NULL,
    entry_b_id      INTEGER NOT NULL,
    similarity      REAL,
    reason          TEXT,
    status          TEXT DEFAULT 'open',  -- open / resolved_a / resolved_b / merged / dismissed
    resolved_by     TEXT,
    resolved_at     TEXT,
    merged_into     INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Dreaming audit log
CREATE TABLE IF NOT EXISTS dreaming_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT DEFAULT (datetime('now')),
    candidates      INTEGER DEFAULT 0,
    promoted        INTEGER DEFAULT 0,
    demoted         INTEGER DEFAULT 0,
    archived        INTEGER DEFAULT 0,
    purged          INTEGER DEFAULT 0,
    conflicts_found INTEGER DEFAULT 0,
    details         TEXT
);

-- Audit log — tracks every write operation per entry
CREATE TABLE IF NOT EXISTS memory_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        INTEGER NOT NULL,
    action          TEXT NOT NULL,       -- create/update/archive/delete/pin/unpin/share/unshare/promote/demote/purge
    agent_id        TEXT,                -- who performed the action
    old_state       TEXT,                -- previous state (for state-change actions)
    new_state       TEXT,                -- resulting state
    details         TEXT,                -- JSON: extra context (title snapshot, etc.)
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_entries_owner ON memory_entries(owner_agent);
CREATE INDEX IF NOT EXISTS idx_entries_scope ON memory_entries(scope);
CREATE INDEX IF NOT EXISTS idx_entries_state ON memory_entries(state);
CREATE INDEX IF NOT EXISTS idx_entries_project ON memory_entries(project);
CREATE INDEX IF NOT EXISTS idx_entries_score ON memory_entries(score DESC);
CREATE INDEX IF NOT EXISTS idx_entries_access ON memory_entries(access_count DESC);
CREATE INDEX IF NOT EXISTS idx_entries_checksum ON memory_entries(checksum);
CREATE INDEX IF NOT EXISTS idx_entries_pinned ON memory_entries(is_pinned);
CREATE INDEX IF NOT EXISTS idx_conflicts_status ON memory_conflicts(status);
CREATE INDEX IF NOT EXISTS idx_audit_entry ON memory_audit_log(entry_id);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON memory_audit_log(agent_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON memory_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON memory_audit_log(created_at DESC);
"""


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
        self.conn.executescript(SCHEMA)
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
                state, last_access_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'raw', datetime('now'))""",
            (content, title, tag_str, source, ck, owner_agent, scope, project),
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
               tags: list = None, scope: str = None, project: str = None) -> Optional[dict]:
        """Update an entry. Only provided fields are changed."""
        row = self.fetchone("SELECT * FROM memory_entries WHERE id = ?", (entry_id,))
        if not row:
            return None

        new_content = content if content is not None else row["content"]
        new_title = title if title is not None else row["title"]
        new_tags = json.dumps(tags, ensure_ascii=False) if tags is not None else row["tags"]
        new_scope = scope if scope is not None else row["scope"]
        new_project = project if project is not None else row["project"]
        ck = hashlib.md5(new_content.encode()).hexdigest()[:12]

        self.execute(
            """UPDATE memory_entries
               SET content = ?, title = ?, tags = ?, checksum = ?,
                   scope = ?, project = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (new_content, new_title, new_tags, ck, new_scope, new_project, entry_id),
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
               tags: list = None, scope: str = None, project: str = None) -> list:
        """FTS5 full-text search with CJK LIKE fallback and visibility filtering."""
        import re

        vis_filter, vis_params = self._build_visibility_filter(agent_id, scope, project)
        has_cjk = bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', query))

        where = ["memory_fts MATCH ?", vis_filter, "e.deleted_at IS NULL", "e.state != 'archived'"]
        params = [query] + vis_params

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

    def get_hot_entries(self, agent_id: str, include_shared: bool = True) -> list:
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
            "by_agent": by_agent,
            "pinned": pinned,
            "open_conflicts": open_conflicts,
            "last_dreaming": last_dreaming["run_at"] if last_dreaming else None,
            "db_path": str(self.path),
        }

    def list_entries(self, agent_id: str = None, scope: str = None, state: str = None,
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
    import struct
    return struct.pack(f"<{len(floats)}f", *floats)


def blob_to_floats(blob: bytes) -> list[float]:
    """Unpack binary blob into float list."""
    import struct
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))
