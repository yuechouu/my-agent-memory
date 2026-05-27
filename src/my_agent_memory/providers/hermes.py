"""Hermes v2 memory provider — my-agent-memory with Hermes MemoryProvider interface.

Wraps MultiAgentStore as a proper Hermes MemoryProvider plugin.
Supports: hybrid search, auto-extract, auto-tags, conflict detection, dreaming.

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
        "Use this to recall facts, preferences, project details, and prior context."
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
        "Save a durable fact to persistent memory. Use for user preferences, "
        "important instructions, project details, and decisions the user expects "
        "you to remember across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact to remember."},
            "title": {"type": "string", "description": "Short descriptive title."},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "scope": {"type": "string", "enum": ["private", "shared"], "description": "Visibility (default: private)."},
        },
        "required": ["content"],
    },
}

MEMORY_PIN_SCHEMA = {
    "name": "memory_pin",
    "description": "Pin a memory entry so it's never auto-archived and always appears in the hot layer.",
    "parameters": {
        "type": "object",
        "properties": {
            "entry_id": {"type": "integer", "description": "The memory entry ID to pin."},
            "unpin": {"type": "boolean", "description": "Set true to unpin instead."},
        },
        "required": ["entry_id"],
    },
}


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
        if not self._store:
            return ""
        try:
            block = self._store.get_system_prompt_block()
            if block and block.strip():
                return block
        except Exception as e:
            logger.debug("system_prompt_block failed: %s", e)
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._store or not query:
            return ""
        try:
            results = self._store.hybrid_search(query, limit=5)
            if not results:
                return ""
            lines = []
            for r in results:
                title = r.get("title", "") or "(no title)"
                content = (r.get("content", "") or "")[:150]
                source = r.get("owner_agent", "")
                marker = "📌 " if r.get("is_pinned") else ""
                id_tag = f"[#{r['id']}]"
                if source and source != self._store.agent_id:
                    lines.append(f"- {id_tag} {marker}**{title}** [{source}]: {content}")
                else:
                    lines.append(f"- {id_tag} {marker}**{title}**: {content}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("prefetch failed: %s", e)
            return ""

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

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [MEMORY_SEARCH_SCHEMA, MEMORY_SAVE_SCHEMA, MEMORY_PIN_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
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
                entry = self._store.save(
                    content=args["content"],
                    title=args.get("title", ""),
                    tags=tags,
                    scope=args.get("scope", "private"),
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
