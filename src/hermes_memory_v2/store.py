"""MultiAgentStore — top-level API for all memory operations.

Usage:
    from hermes_memory_v2 import MultiAgentStore

    store = MultiAgentStore(agent_id="noor")
    store.save("fact", title="Title")
    results = store.search("query")
    store.hybrid_search("semantic query")
"""

import os
from pathlib import Path
from typing import Optional

from hermes_memory_v2.db import Database
from hermes_memory_v2.search import HybridSearch
from hermes_memory_v2.dreaming import DreamingEngine
from hermes_memory_v2.conflicts import ConflictResolver
from hermes_memory_v2.hot_layer import HotLayer
from hermes_memory_v2.validate import validate_sync
from hermes_memory_v2.embed import EmbeddingClient


class MultiAgentStore:
    """Primary entry point for the v2 memory system.

    Compatible with v1 Store API (search, save, get, archive, status, dream, rebuild).
    """

    def __init__(
        self,
        db_path: str = "",
        agent_id: str = "",
        hermes_home: str = "",
        config: dict = None,
    ):
        self.config = config or {}
        self.agent_id = agent_id or os.getenv("HERMES_AGENT_ID", "noor")

        # DB
        self.db = Database(db_path)

        # Hot layer
        hermes_home = hermes_home or os.getenv("HERMES_HOME", "")
        self.hot_layer = HotLayer(self.db, hermes_home=hermes_home) if hermes_home else None

        # Embedding client
        embed_config = self.config.get("embedding", {})
        self.embed_client = None
        api_key = self._get_api_key()
        if api_key:
            self.embed_client = EmbeddingClient(
                api_key=api_key,
                base_url=embed_config.get("base_url", "https://api.siliconflow.cn/v1"),
                model=embed_config.get("model", "Qwen/Qwen3-Embedding-8B"),
                batch_size=embed_config.get("batch_size", 10),
            )

        # Search
        self._search = HybridSearch(self.db, self.embed_client)

        # Dreaming
        self.dreaming_engine = DreamingEngine(
            self.db,
            hot_layer=self.hot_layer,
            embed_client=self.embed_client,
            scoring_config=self.config.get("scoring", {}),
        )

        # Conflicts
        self.conflict_resolver = ConflictResolver(self.db)

    def _get_api_key(self) -> str:
        """Get SiliconFlow API key from config, env, or auth file."""
        key = self.config.get("embedding", {}).get("api_key", "")
        if key:
            return key
        key = os.getenv("SILICONFLOW_API_KEY", "")
        if key:
            return key
        # Try auth file
        auth_path = Path.home() / ".local" / "share" / "kilo" / "auth.json"
        if auth_path.exists():
            import json
            try:
                data = json.loads(auth_path.read_text())
                sf = data.get("siliconflow", {})
                if isinstance(sf, dict):
                    return sf.get("key", "")
                return data.get("siliconflow_key", "") or data.get("api_key", "")
            except Exception:
                pass
        return ""

    # ── CRUD ─────────────────────────────────────────────────

    def save(
        self,
        content: str,
        title: str = "",
        tags: list = None,
        source: str = "manual",
        scope: str = "private",
        project: str = None,
    ) -> dict:
        """Save a new memory entry. Validates content before writing.

        Returns:
            Entry dict on success. Raises ValueError on validation failure.
        """
        valid, error = validate_sync(content, title, tags)
        if not valid:
            raise ValueError(f"Validation failed: {error}")

        result = self.db.insert(
            content=content,
            title=title,
            tags=tags,
            source=source,
            owner_agent=self.agent_id,
            scope=scope,
            project=project,
        )

        # Async embedding generation
        if self.embed_client and result:
            self._schedule_embedding(result["id"], content)

        return result

    def get(self, entry_id: int) -> Optional[dict]:
        """Get a single entry by ID."""
        return self.db.get(entry_id)

    def update(self, entry_id: int, **fields) -> Optional[dict]:
        """Update entry fields. Only provided fields are changed."""
        return self.db.update(entry_id, **fields)

    def archive(self, entry_id: int) -> Optional[dict]:
        """Soft-delete an entry (mark as archived)."""
        return self.db.archive(entry_id)

    def delete(self, entry_id: int) -> bool:
        """Hard-delete an entry (only if already archived)."""
        return self.db.delete(entry_id)

    # ── Pin ──────────────────────────────────────────────────

    def pin(self, entry_id: int) -> Optional[dict]:
        return self.db.pin(entry_id)

    def unpin(self, entry_id: int) -> Optional[dict]:
        return self.db.unpin(entry_id)

    # ── Share ────────────────────────────────────────────────

    def share(self, entry_id: int) -> Optional[dict]:
        """Change scope from private to shared."""
        result = self.db.share(entry_id)
        if result and self.hot_layer:
            self.hot_layer.rebuild_all()
        return result

    def unshare(self, entry_id: int) -> Optional[dict]:
        """Change scope from shared to private (owner only)."""
        result = self.db.unshare(entry_id)
        if result and self.hot_layer:
            self.hot_layer.rebuild_all()
        return result

    # ── Search ───────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        tags: list = None,
        scope: str = None,
        agent_id: str = None,
        source: str = None,
    ) -> list[dict]:
        """FTS5 full-text search with visibility filtering.

        Args:
            query: Search query string.
            limit: Max results.
            offset: Result offset.
            tags: Filter by tags.
            scope: Filter by scope (private/shared/project).
            agent_id: Filter by owner agent. Defaults to self.agent_id.
            source: Filter by source type.
        """
        return self.db.search(
            query,
            agent_id=agent_id or self.agent_id,
            limit=limit,
            offset=offset,
            tags=tags,
            scope=scope,
        )

    def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        scope: str = None,
        agent_id: str = None,
        project: str = None,
        fts_weight: float = 0.5,
        vec_weight: float = 0.5,
    ) -> list[dict]:
        """Hybrid search: FTS5 + vector + RRF fusion.

        Falls back to FTS5-only if vector search is unavailable.
        """
        return self._search.search(
            query,
            agent_id=agent_id or self.agent_id,
            limit=limit,
            scope=scope,
            project=project,
            fts_weight=fts_weight,
            vec_weight=vec_weight,
        )

    # ── Dreaming ─────────────────────────────────────────────

    def dreaming(
        self,
        dry_run: bool = True,
        promote_threshold: float = None,
        demote_threshold: float = None,
        archive_threshold: float = None,
        **kwargs,
    ) -> dict:
        """Run dreaming pass. See DreamingEngine.run() for full options."""
        return self.dreaming_engine.run(
            dry_run=dry_run,
            promote_threshold=promote_threshold or 3.0,
            demote_threshold=demote_threshold or 1.0,
            archive_threshold=archive_threshold or 0.1,
            **kwargs,
        )

    def consolidate(self, entry_ids: list[int]) -> Optional[dict]:
        """Merge multiple entries into one via LLM consolidation.

        Uses DeepSeek Chat to intelligently merge overlapping memories,
        resolve minor contradictions, and keep unique facts.
        Originals are marked as superseded_by the merged result.
        Falls back to simple concatenation if LLM call fails.
        """
        if not entry_ids or len(entry_ids) < 2:
            return None

        entries = []
        for eid in entry_ids:
            entry = self.db.get(eid)
            if entry:
                entries.append(entry)

        if len(entries) < 2:
            return None

        title = "Merged"
        content = ""
        source = "consolidated"

        # Try LLM consolidation
        llm_failed = True
        try:
            from hermes_memory_v2.llm import (
                LLMClient, build_consolidate_messages, parse_consolidate_response,
            )
            llm = LLMClient()
            messages = build_consolidate_messages(entries)
            response = llm.chat(messages, temperature=0.3, max_tokens=800)
            parsed = parse_consolidate_response(response)
            if parsed.get("content"):
                title = parsed["title"] or title
                content = parsed["content"]
                source = "consolidated"
                llm_failed = False
        except Exception as e:
            pass

        if llm_failed:
            # Fallback to simple concatenation
            contents = []
            for e in entries:
                contents.append(f"## {e.get('title', '')}\n{e.get('content', '')}")
            content = "\n\n".join(contents)
            title = entries[0].get("title", "Merged")

        first = entries[0]
        result = self.db.insert(
            content=content,
            title=title,
            source=source,
            owner_agent=first.get("owner_agent", self.agent_id),
            scope=first.get("scope", "private"),
        )

        if result:
            # Mark originals as superseded
            for eid in entry_ids:
                self.db.execute(
                    "UPDATE memory_entries SET superseded_by = ?, state = 'archived' WHERE id = ?",
                    (result["id"], eid),
                )
            self.db.commit()

        return result

    # ── Conflicts ────────────────────────────────────────────

    def get_conflicts(self, status: str = "open") -> list[dict]:
        return self.conflict_resolver.get_open_conflicts() if status == "open" else []

    def resolve_conflict(
        self,
        conflict_id: int,
        strategy: str,
        merged_content: str = None,
        merged_title: str = None,
    ) -> Optional[dict]:
        return self.conflict_resolver.resolve(
            conflict_id=conflict_id,
            strategy=strategy,
            resolved_by=self.agent_id,
            merged_content=merged_content,
            merged_title=merged_title,
        )

    # ── System Prompt Block ──────────────────────────────────

    def get_system_prompt_block(self, agent_id: str = None, max_chars: int = None) -> str:
        """Get hot layer content for system prompt injection."""
        if not self.hot_layer:
            return ""
        return self.hot_layer.get_system_prompt_block(
            agent_id=agent_id or self.agent_id,
            max_chars=max_chars,
        )

    # ── Stats ────────────────────────────────────────────────

    def stats(self) -> dict:
        return self.db.stats()

    def list_entries(
        self, agent_id: str = None, scope: str = None, state: str = None,
        page: int = 1, limit: int = 20, query: str = None,
    ) -> dict:
        """Paginated entry listing (for dashboard API)."""
        return self.db.list_entries(
            agent_id=agent_id, scope=scope, state=state,
            page=page, limit=limit, query=query,
        )

    # ── Maintenance ──────────────────────────────────────────

    def rebuild(self):
        """Rebuild FTS5 index."""
        self.db.rebuild_fts()

    def rebuild_hot_layer(self):
        """Regenerate all hot layer Markdown files."""
        if self.hot_layer:
            self.hot_layer.rebuild_all()

    def close(self):
        self.db.close()

    # ── Internal ─────────────────────────────────────────────

    def _schedule_embedding(self, entry_id: int, content: str):
        """Generate embedding for a new entry (runs synchronously for simplicity).

        For production async, this would use threading or a background queue.
        """
        if not self.embed_client:
            return
        try:
            embedding = self.embed_client.embed(content)
            if embedding:
                import struct
                blob = struct.pack(f"<{len(embedding)}f", *embedding)
                self.db.set_embedding(entry_id, blob, self.embed_client.model)
                # Also index for vector search
                self.db.index_vector(entry_id, embedding)
        except Exception:
            pass  # Embedding is best-effort, not critical

    def embed_pending(self, limit: int = 50) -> int:
        """Generate embeddings for entries that don't have them yet."""
        if not self.embed_client:
            return 0

        entries = self.db.get_entries_without_embedding(limit=limit)
        if not entries:
            return 0

        texts = [e["content"] for e in entries]
        embeddings = self.embed_client.embed_batch(texts)

        import struct
        count = 0
        for entry, emb in zip(entries, embeddings):
            if emb:
                blob = struct.pack(f"<{len(emb)}f", *emb)
                self.db.set_embedding(entry["id"], blob, self.embed_client.model)
                self.db.index_vector(entry["id"], emb)
                count += 1

        return count


# ── V1-compatible Store class ────────────────────────────────
#
# Installed via pip as 'hermes-memory-v2', but noor imports
# 'from hermes_memory import Store'. v2 provides this compatibility
# shim so noor's code doesn't need to change.

class Store(MultiAgentStore):
    """V1-compatible Store for drop-in replacement.

    Usage (identical to v1):
        from hermes_memory import Store
        store = Store()
        store.search("query")
        store.save("fact", title="title")
    """

    def __init__(self, db_path: str = ""):
        super().__init__(db_path=db_path)
        # In v1 compatibility mode, default agent_id from env or 'noor'

    # v1 method name aliases
    status = MultiAgentStore.stats
    dream = MultiAgentStore.dreaming
