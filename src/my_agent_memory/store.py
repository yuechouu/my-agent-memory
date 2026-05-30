"""MultiAgentStore — top-level API for all memory operations.

Usage:
    from my_agent_memory import MultiAgentStore

    store = MultiAgentStore(agent_id="noor")
    store.save("fact", title="Title")
    results = store.search("query")
    store.hybrid_search("semantic query")
"""

import os
from pathlib import Path
from typing import Optional

from my_agent_memory.db import Database
from my_agent_memory.search import HybridSearch
from my_agent_memory.dreaming import DreamingEngine
from my_agent_memory.conflicts import ConflictResolver
from my_agent_memory.hot_layer import HotLayer
from my_agent_memory.validate import validate_sync
from my_agent_memory.embed import EmbeddingClient
from my_agent_memory.config import get_api_key
from my_agent_memory.rag import RAGEngine


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

        # Reranker (optional, same API key as embedding)
        self._reranker = None
        if api_key:
            try:
                from my_agent_memory.reranker import Reranker
                reranker_config = self.config.get("reranker", {})
                self._reranker = Reranker(
                    api_key=api_key,
                    base_url=reranker_config.get("base_url", embed_config.get("base_url", "https://api.siliconflow.cn/v1")),
                    model=reranker_config.get("model", "BAAI/bge-reranker-v2-m3"),
                )
            except Exception:
                pass  # Best-effort

        # Search
        self._search = HybridSearch(self.db, self.embed_client, self._reranker)

        # Dreaming
        self.dreaming_engine = DreamingEngine(
            self.db,
            hot_layer=self.hot_layer,
            embed_client=self.embed_client,
            scoring_config=self.config.get("scoring", {}),
        )

        # Conflicts
        self.conflict_resolver = ConflictResolver(self.db)

        # TagGraph
        from my_agent_memory.tag_graph import TagGraph
        self.tag_graph = TagGraph(self.db)

        # RAG Engine
        self.rag = RAGEngine(db=self.db, embed_client=self.embed_client)

        # Thread tracking for clean shutdown
        self._pending_threads = []

    def _get_api_key(self) -> str:
        """Get SiliconFlow API key from config, env, or auth file."""
        # 1. Explicit config passed to constructor
        key = self.config.get("embedding", {}).get("api_key", "")
        if key:
            return key
        # 2. Env var > auth file
        return get_api_key(
            "siliconflow",
            env_var="SILICONFLOW_API_KEY",
            config_key="siliconflow_key",
        )

    # ── CRUD ─────────────────────────────────────────────────

    def save(
        self,
        content: str,
        title: str = "",
        tags: list = None,
        source: str = "manual",
        scope: str = "private",
        project: str = None,
        memory_type: str = None,
    ) -> dict:
        """Save a new memory entry. Validates content before writing.

        Args:
            memory_type: One of 'procedural', 'entity', 'knowledge'.
                         If None, defaults to 'knowledge' and auto-detected async.

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
            memory_type=memory_type or "knowledge",
            audit_agent=self.agent_id,
        )

        # Async embedding generation
        if self.embed_client and result:
            self._schedule_embedding(result["id"], content)

        # Async LLM validation (second-layer security check)
        if result:
            self._schedule_async_validation(result["id"], content, title)

        # Async tag suggestion when no tags provided
        if result and not tags:
            self._schedule_tag_suggestion(result["id"], content, title, memory_type=memory_type or "knowledge")

        # Async type detection when not explicitly provided
        if result and not memory_type:
            self._schedule_type_detection(result["id"], content, title)

        # Update tag co-occurrence graph
        if result and tags and len(tags) >= 2:
            try:
                self.tag_graph.update_cooccurrence(tags)
            except Exception:
                pass  # Best-effort

        return result

    def get(self, entry_id: int) -> Optional[dict]:
        """Get a single entry by ID."""
        return self.db.get(entry_id)

    def update(self, entry_id: int, **fields) -> Optional[dict]:
        """Update entry fields. Only provided fields are changed."""
        return self.db.update(entry_id, **fields)

    def archive(self, entry_id: int) -> Optional[dict]:
        """Soft-delete an entry (mark as archived)."""
        return self.db.archive(entry_id, audit_agent=self.agent_id)

    def delete(self, entry_id: int) -> bool:
        """Hard-delete an entry (only if already archived)."""
        return self.db.delete(entry_id, audit_agent=self.agent_id)

    # ── Pin ──────────────────────────────────────────────────

    def pin(self, entry_id: int) -> Optional[dict]:
        return self.db.pin(entry_id, audit_agent=self.agent_id)

    def unpin(self, entry_id: int) -> Optional[dict]:
        return self.db.unpin(entry_id, audit_agent=self.agent_id)

    # ── Share ────────────────────────────────────────────────

    def share(self, entry_id: int) -> Optional[dict]:
        """Change scope from private to shared."""
        result = self.db.share(entry_id, audit_agent=self.agent_id)
        if result and self.hot_layer:
            self.hot_layer.rebuild_all()
        return result

    def unshare(self, entry_id: int) -> Optional[dict]:
        """Change scope from shared to private (owner only)."""
        result = self.db.unshare(entry_id, audit_agent=self.agent_id)
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
        memory_type: str = None,
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
            memory_type: Filter by memory type (procedural/entity/knowledge).
        """
        return self.db.search(
            query,
            agent_id=agent_id or self.agent_id,
            limit=limit,
            offset=offset,
            tags=tags,
            scope=scope,
            memory_type=memory_type,
        )

    def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        scope: str = None,
        agent_id: str = None,
        project: str = None,
        memory_type: str = None,
        fts_weight: float = 0.5,
        vec_weight: float = 0.5,
        rerank: bool = False,
    ) -> list[dict]:
        """Hybrid search: FTS5 + vector + RRF fusion, optional reranking.

        Falls back to FTS5-only if vector search is unavailable.
        """
        return self._search.search(
            query,
            agent_id=agent_id or self.agent_id,
            limit=limit,
            scope=scope,
            project=project,
            memory_type=memory_type,
            fts_weight=fts_weight,
            vec_weight=vec_weight,
            rerank=rerank,
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
            promote_threshold=promote_threshold if promote_threshold is not None else 3.0,
            demote_threshold=demote_threshold if demote_threshold is not None else 1.0,
            archive_threshold=archive_threshold if archive_threshold is not None else 0.1,
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
            from my_agent_memory.llm import (
                LLMClient, build_consolidate_messages, parse_consolidate_response,
            )
            llm = LLMClient()
            messages = build_consolidate_messages(entries)
            response = llm.chat(messages, temperature=0.3, max_tokens=2000)
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
        memory_type: str = None,
        page: int = 1, limit: int = 20, query: str = None,
        sort_by: str = "", sort_order: str = "desc",
    ) -> dict:
        """Paginated entry listing (for dashboard API)."""
        return self.db.list_entries(
            agent_id=agent_id, scope=scope, state=state,
            memory_type=memory_type,
            page=page, limit=limit, query=query,
            sort_by=sort_by, sort_order=sort_order,
        )

    # ── Unified Search ──────────────────────────────────────────

    def unified_search(
        self,
        query: str,
        domain: str = None,
        limit: int = 5,
        include_memories: bool = True,
        include_learned: bool = True,
        include_rag: bool = True,
    ) -> dict:
        """Unified search across structured memories, learned knowledge, and RAG documents.

        Args:
            query: Search query
            domain: Filter RAG results by domain
            limit: Max results per category
            include_memories: Search structured memories
            include_learned: Search learned memories
            include_rag: Search RAG documents

        Returns:
            Dict with memories, learned, rag results and total count
        """
        result = {
            "memories": [],
            "learned": [],
            "rag": [],
            "total": 0,
        }

        # 1. Structured memories
        if include_memories:
            memories = self.hybrid_search(query, limit=limit)
            for m in memories:
                m.pop("embedding", None)
            result["memories"] = memories

        # 2. Learned memories
        if include_learned:
            learned = self.hybrid_search(
                query,
                limit=limit,
                memory_types=["learned-research", "learned-solution", "learned-summary", "learned-pattern"],
            )
            for l in learned:
                l.pop("embedding", None)
            result["learned"] = learned

        # 3. RAG documents
        if include_rag:
            rag_results = self.rag.search(query, domain=domain, limit=limit)
            result["rag"] = rag_results

        result["total"] = len(result["memories"]) + len(result["learned"]) + len(result["rag"])
        return result

    # ── Maintenance ──────────────────────────────────────────

    def rebuild(self):
        """Rebuild FTS5 index."""
        self.db.rebuild_fts()

    def rebuild_hot_layer(self):
        """Regenerate all hot layer Markdown files."""
        if self.hot_layer:
            self.hot_layer.rebuild_all()

    def close(self):
        # Wait for background threads to finish before closing db
        for t in self._pending_threads:
            t.join(timeout=5.0)
        self._pending_threads.clear()
        self.db.close()

    # ── Internal ─────────────────────────────────────────────

    def _schedule_embedding(self, entry_id: int, content: str):
        """Generate embedding for a new entry in a background thread.

        Non-blocking — runs in a daemon thread so save() returns immediately.
        """
        if not self.embed_client:
            return

        import threading

        def _run():
            try:
                embedding = self.embed_client.embed(content)
                if embedding:
                    import struct
                    blob = struct.pack(f"<{len(embedding)}f", *embedding)
                    self.db.set_embedding(entry_id, blob, self.embed_client.model)
                    self.db.index_vector(entry_id, embedding)
            except Exception:
                pass  # Embedding is best-effort, not critical

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._pending_threads.append(t)

    # ── Async validation ─────────────────────────────────────

    def _schedule_async_validation(self, entry_id: int, content: str, title: str = ""):
        """Run async LLM validation for a newly saved entry.
        
        Called after save completes. Non-blocking — runs in a daemon thread.
        Updates validation_status on the entry when done.
        """
        import threading

        def _run():
            try:
                from my_agent_memory.validate import validate_async
                status = validate_async(content, title)
                self.db.set_validation_status(entry_id, status)
            except Exception:
                self.db.set_validation_status(entry_id, "error")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._pending_threads.append(t)

    def _schedule_tag_suggestion(self, entry_id: int, content: str, title: str = "",
                                  memory_type: str = "knowledge"):
        """Suggest tags for an entry via LLM if none were provided.

        Uses existing tag pool for context-aware suggestions.
        Runs asynchronously in a daemon thread. Updates the entry's tags when done.
        """
        import threading

        def _run():
            try:
                from my_agent_memory.llm import (
                    LLMClient, build_suggest_tags_messages, parse_suggest_tags_response,
                )
                from my_agent_memory.validate import validate_tags

                # Get existing tags for context
                try:
                    existing_tags = self.db.get_tag_frequencies(limit=30)
                except Exception:
                    existing_tags = []

                llm = LLMClient()
                messages = build_suggest_tags_messages(title, content, memory_type, existing_tags)
                response = llm.chat(messages, temperature=0.1, max_tokens=100)
                if response:
                    tags = parse_suggest_tags_response(response)
                    if tags:
                        valid, _ = validate_tags(tags)
                        if valid:
                            self.db.update(entry_id, tags=tags)
            except Exception:
                pass  # Best-effort, non-critical

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._pending_threads.append(t)

    def _schedule_type_detection(self, entry_id: int, content: str, title: str = ""):
        """Detect memory type via LLM if not explicitly set.

        Runs asynchronously in a daemon thread. Updates the entry's memory_type when done.
        """
        import threading

        def _run():
            try:
                from my_agent_memory.llm import (
                    LLMClient, build_type_detect_messages, parse_type_detect_response,
                )
                llm = LLMClient()
                messages = build_type_detect_messages(title, content)
                response = llm.chat(messages, temperature=0.1, max_tokens=20)
                if response:
                    detected = parse_type_detect_response(response)
                    if detected:
                        self.db.update(entry_id, memory_type=detected)
            except Exception:
                pass  # Best-effort, non-critical

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._pending_threads.append(t)

    def validate_pending(self, limit: int = 20) -> int:
        """Run async validation for entries that haven't been checked yet."""
        entries = self.db.get_unvalidated(limit=limit)
        if not entries:
            return 0

        from my_agent_memory.validate import validate_async
        from my_agent_memory.llm import LLMClient
        llm = LLMClient()

        count = 0
        for entry in entries:
            try:
                status = validate_async(
                    entry.get("content", ""),
                    entry.get("title", ""),
                    llm_client=llm,
                )
                self.db.set_validation_status(entry["id"], status)
                count += 1
            except Exception:
                self.db.set_validation_status(entry["id"], "error")
                count += 1

        return count

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
# Installed via pip as 'my-agent-memory', but noor imports
# 'from my_agent_memory import Store'. v2 provides this compatibility
# shim so noor's code doesn't need to change.

class Store(MultiAgentStore):
    """V1-compatible Store for drop-in replacement.

    Usage (identical to v1):
        from my_agent_memory import Store
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
