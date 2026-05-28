"""Hermes v2 memory provider — my-agent-memory with Hermes MemoryProvider interface.

Wraps MultiAgentStore as a proper Hermes MemoryProvider plugin.
Supports: hybrid search, auto-extract, auto-tags, conflict detection, dreaming.

Three-layer automation:
  Layer 1 — Tool descriptions with trigger conditions (LLM自主判断)
  Layer 2 — System prompt guidelines via system_prompt_block() (全局记忆使用规则)
  Layer 3 — Event fallbacks via MemoryProvider hooks:
    - on_turn_start: detect save intent, trigger fallback prefetch
    - handle_tool_call: track if agent searched this turn
    - prefetch: serve fallback results when agent didn't search
    - sync_turn: auto-extract memories from conversation turns
    - on_session_end: extract key facts at session end

Config in $HERMES_HOME/config.yaml:
  plugins:
    hermes-v2:
      db_path: $HERMES_HOME/memories/memory_v2.db   # omit for default
      agent_id: hermes                                # omit for default
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas exposed to the model
# ---------------------------------------------------------------------------

MEMORY_SEARCH_SCHEMA = {
    "name": "memory_search",
    "description": (
        "Search the agent's persistent memory using hybrid FTS5 + vector search. "
        "**Use when**: user asks about past interactions, preferences, project details, "
        "or before answering questions that might be in memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {"type": "integer", "description": "Max results (default 5)."},
        },
        "required": ["query"],
    },
}

MEMORY_SAVE_SCHEMA = {
    "name": "memory_save",
    "description": (
        "Save a durable fact to persistent memory. "
        "**Use when**: user shares important preferences, decisions, instructions, "
        "or explicitly says 'remember this'. Do NOT save temporary context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact to remember."},
            "title": {"type": "string", "description": "Short descriptive title."},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "scope": {"type": "string", "enum": ["private", "shared"], "description": "Visibility (default: private)."},
            "memory_type": {"type": "string", "enum": ["procedural", "entity", "knowledge"], "description": "Memory type. Auto-detected if omitted."},
        },
        "required": ["content"],
    },
}

MEMORY_PIN_SCHEMA = {
    "name": "memory_pin",
    "description": (
        "Pin a memory entry so it's never auto-archived and always appears in the hot layer. "
        "**Use when**: user emphasizes something is critical and should never be auto-archived."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entry_id": {"type": "integer", "description": "The memory entry ID to pin."},
            "unpin": {"type": "boolean", "description": "Set true to unpin instead."},
        },
        "required": ["entry_id"],
    },
}

MEMORY_RECALL_SCHEMA = {
    "name": "memory_recall",
    "description": (
        "Structured recall with filters. "
        "**Use when**: you need precise filtering by type, scope, or tags. "
        "Prefer memory_search for simple queries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "memory_type": {"type": "string", "enum": ["procedural", "entity", "knowledge"], "description": "Filter by memory type."},
            "scope": {"type": "string", "enum": ["private", "shared"], "description": "Filter by scope."},
            "tags": {"type": "string", "description": "Comma-separated tags to filter by."},
            "limit": {"type": "integer", "description": "Max results (default 5)."},
        },
        "required": ["query"],
    },
}

MEMORY_LIST_SCHEMA = {
    "name": "memory_list",
    "description": "List recent memory entries with optional filters and pagination.",
    "parameters": {
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["raw", "promoted", "hot", "archived"], "description": "Filter by state."},
            "scope": {"type": "string", "enum": ["private", "shared", "project"], "description": "Filter by scope."},
            "memory_type": {"type": "string", "enum": ["procedural", "entity", "knowledge"], "description": "Filter by type."},
            "page": {"type": "integer", "description": "Page number (default 1)."},
            "limit": {"type": "integer", "description": "Results per page (default 10)."},
        },
    },
}

MEMORY_UPDATE_SCHEMA = {
    "name": "memory_update",
    "description": (
        "Update the content, title, or tags of an existing memory entry. "
        "**Use when**: user corrects previously stored information or you notice contradictions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entry_id": {"type": "integer", "description": "The memory entry ID to update."},
            "content": {"type": "string", "description": "New content (omit to keep current)."},
            "title": {"type": "string", "description": "New title (omit to keep current)."},
            "tags": {"type": "string", "description": "New comma-separated tags (omit to keep current)."},
        },
        "required": ["entry_id"],
    },
}

MEMORY_ARCHIVE_SCHEMA = {
    "name": "memory_archive",
    "description": (
        "Archive (soft-delete) a memory entry. "
        "**Use when**: information is no longer relevant or user asks to forget."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entry_id": {"type": "integer", "description": "The memory entry ID to archive."},
        },
        "required": ["entry_id"],
    },
}

MEMORY_DREAM_SCHEMA = {
    "name": "memory_dream",
    "description": (
        "Run a dreaming lifecycle pass. Promotes popular memories, demotes stale ones, "
        "archives unused ones. Default is dry-run (preview only)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dry_run": {"type": "boolean", "description": "If true, only preview changes (default true)."},
        },
    },
}

MEMORY_CONFLICTS_SCHEMA = {
    "name": "memory_conflicts",
    "description": "View open memory conflicts or resolve a specific conflict.",
    "parameters": {
        "type": "object",
        "properties": {
            "conflict_id": {"type": "integer", "description": "Conflict ID to resolve (omit to list open conflicts)."},
            "strategy": {"type": "string", "enum": ["last_write_wins", "keep_both", "merge", "dismiss"], "description": "Resolution strategy."},
            "merged_content": {"type": "string", "description": "Merged content (required if strategy is 'merge')."},
        },
    },
}

MEMORY_TAG_GRAPH_SCHEMA = {
    "name": "memory_tag_graph",
    "description": "Explore tag relationships and co-occurrence patterns in memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "tag": {"type": "string", "description": "Tag to find related tags for."},
            "action": {"type": "string", "enum": ["related", "stats"], "description": "Action: 'related' finds co-occurring tags, 'stats' shows graph overview."},
        },
    },
}

# ---------------------------------------------------------------------------
# Layer 2: Memory guidelines for system prompt
# ---------------------------------------------------------------------------

MEMORY_GUIDELINES = """
## Memory System
You have access to a persistent memory system. Use it actively.

### When to Search
- User asks about past interactions, preferences, or project details
- Before answering questions that might be in memory

### When to Save
- User shares important preferences, decisions, instructions
- User explicitly says to remember something
- You learn durable facts (not temporary context)

### When to Update
- User corrects previously stored information
- You notice contradictions between conversation and memory

### Memory Types
- procedural: workflows, how-to steps, instructions
- entity: facts about specific things (servers, tools, projects)
- knowledge: general concepts, theories, configurations
""".strip()

# ---------------------------------------------------------------------------
# Layer 3: Keyword lists for fallback detection
# ---------------------------------------------------------------------------

SAVE_KEYWORDS = [
    "记住", "记下来", "别忘了", "不要忘记", "记一下", "帮我记", "保存这个", "记录下来",
    "remember this", "save this", "note that", "don't forget", "keep in mind",
]

MEMORY_KEYWORDS = [
    "记得", "记住", "忘记", "之前", "上次", "以前", "说过", "聊过", "提到", "告诉过",
    "remember", "recall", "before", "previously", "mentioned",
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_plugin_config() -> dict:
    try:
        from hermes_constants import get_hermes_home
        from hermes_cli.config import cfg_get
        config_path = get_hermes_home() / "config.yaml"
        if not config_path.exists():
            return {}
        import yaml
        with open(config_path, encoding="utf-8-sig") as f:
            all_config = yaml.safe_load(f) or {}
        return cfg_get(all_config, "plugins", "hermes-v2", default={}) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class HermesV2Provider:
    """Hermes MemoryProvider backed by my-agent-memory MultiAgentStore.

    Imports MemoryProvider ABC lazily so this module can be loaded
    standalone (e.g. for testing) without the Hermes runtime.
    """

    def __init__(self, config: dict | None = None):
        self._config = config or _load_plugin_config()
        self._store = None
        self._session_id = ""
        self._agent_searched_this_turn = False
        self._save_flagged = False
        self._last_user_message = ""
        self._pending_prefetch = None

    @property
    def name(self) -> str:
        return "hermes-v2"

    def is_available(self) -> bool:
        try:
            import my_agent_memory
            return True
        except ImportError:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        from my_agent_memory.store import MultiAgentStore

        hermes_home = kwargs.get("hermes_home") or os.getenv("HERMES_HOME", "")
        db_path = self._config.get("db_path", "")
        agent_id = self._config.get("agent_id", "hermes")

        if db_path and hermes_home:
            db_path = db_path.replace("$HERMES_HOME", hermes_home).replace("${HERMES_HOME}", hermes_home)

        self._store = MultiAgentStore(
            db_path=db_path,
            agent_id=agent_id,
            hermes_home=hermes_home,
        )
        self._session_id = session_id
        logger.info("Hermes v2 memory provider initialized (agent=%s)", agent_id)

    def system_prompt_block(self) -> str:
        parts = []
        if self._store:
            try:
                block = self._store.get_system_prompt_block()
                if block and block.strip():
                    parts.append(block)
            except Exception as e:
                logger.debug("system_prompt_block failed: %s", e)
        parts.append(MEMORY_GUIDELINES)
        return "\n\n".join(parts)

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Layer 3: track search state, detect save intent, trigger fallback prefetch."""
        self._last_user_message = message or ""

        # Detect explicit save intent
        if any(kw in self._last_user_message for kw in SAVE_KEYWORDS):
            self._save_flagged = True

        # Fallback prefetch: if agent didn't search last turn and user message
        # contains memory keywords, queue a prefetch for this turn
        if not self._agent_searched_this_turn and self._store:
            if any(kw in self._last_user_message for kw in MEMORY_KEYWORDS):
                try:
                    results = self._store.hybrid_search(self._last_user_message, limit=3)
                    if results:
                        self._pending_prefetch = results
                except Exception as e:
                    logger.debug("Fallback prefetch failed: %s", e)

        # Reset for new turn
        self._agent_searched_this_turn = False

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        # Use fallback prefetch results if available (from on_turn_start)
        if self._pending_prefetch is not None:
            results = self._pending_prefetch
            self._pending_prefetch = None
            return self._format_prefetch_results(results)

        if not self._store or not query:
            return ""
        try:
            results = self._store.hybrid_search(query, limit=5)
            return self._format_prefetch_results(results)
        except Exception as e:
            logger.debug("prefetch failed: %s", e)
            return ""

    def _format_prefetch_results(self, results: list) -> str:
        if not results:
            return ""
        lines = []
        for r in results:
            title = r.get("title", "") or "(no title)"
            content = (r.get("content", "") or "")[:150]
            source = r.get("owner_agent", "")
            marker = "📌 " if r.get("is_pinned") else ""
            id_tag = f"[#{r['id']}]"
            if source and self._store and source != self._store.agent_id:
                lines.append(f"- {id_tag} {marker}**{title}** [{source}]: {content}")
            else:
                lines.append(f"- {id_tag} {marker}**{title}**: {content}")
        return "\n".join(lines)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Auto-extract memorable facts from conversation turn."""
        if not self._store:
            return
        # Skip trivial turns
        if len(user_content.strip()) < 20:
            return

        def _extract():
            try:
                from my_agent_memory.llm import (
                    LLMClient, build_extract_messages, parse_extract_response,
                )
                llm = LLMClient()
                messages = build_extract_messages(user_content, assistant_content)
                response = llm.chat(messages, temperature=0.1, max_tokens=300)
                if not response:
                    return
                result = parse_extract_response(response)
                if not result:
                    return
                self._store.save(
                    content=result["content"],
                    title=result["title"],
                    tags=result.get("tags", []),
                    source="auto_extract",
                )
                logger.info("Auto-extracted memory: %s", result["title"][:60])
            except Exception as e:
                logger.debug("Memory extraction failed (non-critical): %s", e)

        threading.Thread(target=_extract, daemon=True).start()
        self._save_flagged = False

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            MEMORY_SEARCH_SCHEMA, MEMORY_SAVE_SCHEMA, MEMORY_PIN_SCHEMA,
            MEMORY_RECALL_SCHEMA, MEMORY_LIST_SCHEMA, MEMORY_UPDATE_SCHEMA,
            MEMORY_ARCHIVE_SCHEMA, MEMORY_DREAM_SCHEMA, MEMORY_CONFLICTS_SCHEMA,
            MEMORY_TAG_GRAPH_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        # Track search calls for fallback prefetch logic
        if tool_name in ("memory_search", "memory_recall"):
            self._agent_searched_this_turn = True

        if not self._store:
            return json.dumps({"error": "Memory store not initialized"})

        try:
            if tool_name == "memory_search":
                results = self._store.hybrid_search(
                    args["query"],
                    limit=int(args.get("limit", 5)),
                )
                # Strip embedding blobs for JSON serialization
                for r in results:
                    r.pop("embedding", None)
                return json.dumps({"results": results, "count": len(results)})

            elif tool_name == "memory_save":
                tags = []
                raw_tags = args.get("tags", "")
                if raw_tags:
                    tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
                memory_type = args.get("memory_type", "")
                entry = self._store.save(
                    content=args["content"],
                    title=args.get("title", ""),
                    tags=tags,
                    scope=args.get("scope", "private"),
                    memory_type=memory_type or None,
                )
                entry.pop("embedding", None)
                return json.dumps({"status": "saved", "entry": entry})

            elif tool_name == "memory_pin":
                entry_id = int(args["entry_id"])
                if args.get("unpin"):
                    result = self._store.unpin(entry_id)
                else:
                    result = self._store.pin(entry_id)
                if result:
                    result.pop("embedding", None)
                return json.dumps({"status": "ok", "entry": result})

            elif tool_name == "memory_recall":
                tags = None
                raw_tags = args.get("tags", "")
                if raw_tags:
                    tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
                results = self._store.search(
                    args["query"],
                    limit=int(args.get("limit", 5)),
                    tags=tags,
                    scope=args.get("scope"),
                    memory_type=args.get("memory_type"),
                )
                for r in results:
                    r.pop("embedding", None)
                return json.dumps({"results": results, "count": len(results)})

            elif tool_name == "memory_list":
                result = self._store.list_entries(
                    state=args.get("state"),
                    scope=args.get("scope"),
                    memory_type=args.get("memory_type"),
                    page=int(args.get("page", 1)),
                    limit=int(args.get("limit", 10)),
                )
                for r in result.get("entries", []):
                    r.pop("embedding", None)
                return json.dumps(result)

            elif tool_name == "memory_update":
                fields = {}
                if args.get("content"):
                    fields["content"] = args["content"]
                if args.get("title"):
                    fields["title"] = args["title"]
                if args.get("tags"):
                    fields["tags"] = [t.strip() for t in args["tags"].split(",") if t.strip()]
                result = self._store.update(int(args["entry_id"]), **fields)
                if result:
                    result.pop("embedding", None)
                return json.dumps({"status": "updated", "entry": result})

            elif tool_name == "memory_archive":
                result = self._store.archive(int(args["entry_id"]))
                if result:
                    result.pop("embedding", None)
                return json.dumps({"status": "archived", "entry": result})

            elif tool_name == "memory_dream":
                dry_run = args.get("dry_run", True)
                report = self._store.dreaming(dry_run=dry_run)
                return json.dumps(report)

            elif tool_name == "memory_conflicts":
                conflict_id = args.get("conflict_id")
                if conflict_id:
                    strategy = args.get("strategy", "dismiss")
                    result = self._store.resolve_conflict(
                        conflict_id=int(conflict_id),
                        strategy=strategy,
                        merged_content=args.get("merged_content"),
                    )
                    return json.dumps({"status": "resolved", "conflict": result})
                else:
                    conflicts = self._store.get_conflicts("open")
                    return json.dumps({"conflicts": conflicts, "count": len(conflicts)})

            elif tool_name == "memory_tag_graph":
                action = args.get("action", "related")
                if action == "stats":
                    stats = self._store.tag_graph.get_tag_stats()
                    return json.dumps(stats)
                else:
                    tag = args.get("tag", "")
                    if not tag:
                        return json.dumps({"error": "Tag is required for 'related' action"})
                    related = self._store.tag_graph.get_related_tags(tag)
                    return json.dumps({"tag": tag, "related": related, "count": len(related)})

            from tools.registry import tool_error
            return tool_error(f"Unknown tool: {tool_name}")

        except Exception as e:
            try:
                from tools.registry import tool_error
                return tool_error(f"Memory tool '{tool_name}' failed: {e}")
            except ImportError:
                return json.dumps({"error": f"Memory tool '{tool_name}' failed: {e}"})

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Extract key facts from the full session on end."""
        if not self._store or not messages:
            return

        def _extract_session():
            try:
                from my_agent_memory.llm import LLMClient

                # Build a summary of the last 10 turns
                recent = messages[-20:] if len(messages) > 20 else messages
                parts = []
                for msg in recent:
                    role = msg.get("role", "user")
                    content = (msg.get("content", "") or "")[:300]
                    parts.append(f"{role}: {content}")

                conversation = "\n".join(parts)
                if len(conversation.strip()) < 100:
                    return

                prompt = (
                    "Extract the 1-3 most important durable facts from this session "
                    "(user preferences, key decisions, important info). "
                    "If nothing worth remembering, say NOTHING.\n\n"
                    "Format for each fact:\nTitle: <title>\nContent: <fact>\nTags: <tags>\n\n"
                    f"Conversation:\n{conversation[:3000]}"
                )

                llm = LLMClient()
                response = llm.chat([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=500)
                if not response or "NOTHING" in response.upper():
                    return

                # Parse multiple facts
                from my_agent_memory.llm import parse_extract_response
                result = parse_extract_response(response)
                if result:
                    self._store.save(
                        content=result["content"],
                        title=result["title"],
                        tags=result.get("tags", []),
                        source="session_extract",
                    )
                    logger.info("Session-end extracted memory: %s", result["title"][:60])

            except Exception as e:
                logger.debug("Session extraction failed (non-critical): %s", e)

        threading.Thread(target=_extract_session, daemon=True).start()

    def shutdown(self) -> None:
        if self._store:
            try:
                self._store.close()
            except Exception:
                pass
            self._store = None

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        from pathlib import Path
        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml
            existing = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8-sig") as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("plugins", {})
            existing["plugins"]["hermes-v2"] = values
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception:
            pass

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "db_path", "description": "SQLite database path", "default": "$HERMES_HOME/memories/memory_v2.db"},
            {"key": "agent_id", "description": "Agent identifier", "default": "hermes"},
        ]


def register(ctx):
    """Called by hermes-agent's plugin loader. ctx has register_memory_provider()."""
    from agent.memory_provider import MemoryProvider

    class _HermesProvider(HermesV2Provider, MemoryProvider):
        pass

    ctx.register_memory_provider(_HermesProvider())
