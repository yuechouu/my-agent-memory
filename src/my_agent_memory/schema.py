"""SQLite schema definition and migration helpers for My Agent Memory."""

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

    -- Async security validation (validate.py LLM secondary check)
    validation_status TEXT,   -- NULL=unchecked, clean, flagged:reason, error

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


def add_column_if_missing(conn, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't already exist (SQLite-safe)."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {r["name"] for r in rows}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
