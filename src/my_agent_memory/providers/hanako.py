"""HanakoProvider — v2 memory backend for Hanako via MemoryProvider ABC.

Plugs into Hanako's MemoryManager as the external provider.
Configured via a JSON file in HERMES_HOME/providers/.

Config format:
{
  "module": "my_agent_memory.providers.hanako",
  "class": "HanakoProvider",
  "agent_id": "hanako",
  "db_path": "E:/hermes/hermes-data/memories/memory_v2.db"
}
"""

import logging
import os
import threading
from pathlib import Path

from my_agent_memory.provider import MemoryProvider

logger = logging.getLogger("my-agent-memory.hanako")


class HanakoProvider(MemoryProvider):
    """v2 memory backend for Hanako — hybrid search + hot layer injection."""

    def __init__(self, config: dict):
        from my_agent_memory.store import MultiAgentStore

        self.config = config
        self.agent_id = config.get("agent_id", "hanako")
        self.db_path = config.get("db_path", "")
        hermes_home = config.get("hermes_home") or os.getenv("HERMES_HOME", "")
        self._store = None
        self._store_config = {
            "db_path": self.db_path,
            "agent_id": self.agent_id,
            "hermes_home": hermes_home,
        }

    @property
    def store(self):
        """Lazy init the MultiAgentStore."""
        if self._store is None:
            from my_agent_memory.store import MultiAgentStore
            self._store = MultiAgentStore(
                db_path=self._store_config["db_path"],
                agent_id=self._store_config["agent_id"],
                hermes_home=self._store_config["hermes_home"],
            )
        return self._store

    def prefetch(self, query: str) -> str:
        """Per-turn memory recall via hybrid search.

        Results are appended to Hanako's message context (not system prompt).
        """
        results = self.store.hybrid_search(query, limit=5, agent_id=self.agent_id)
        if not results:
            return ""

        lines = []
        for r in results:
            title = r.get("title", "") or "(no title)"
            content = r.get("content", "")[:150]
            source = r.get("owner_agent", "")
            marker = "📌 " if r.get("is_pinned") else ""
            if source != self.agent_id:
                lines.append(f"- {marker}**{title}** [{source}]: {content}")
            else:
                lines.append(f"- {marker}**{title}**: {content}")

        return "\n".join(lines)

    def system_prompt_block(self) -> str:
        """Hot layer content for Hanako's system prompt volatile layer.

        Returns agent-specific entries + shared entries, sorted by score.
        Hanako's MemoryManager truncates to its own token budget.
        """
        return self.store.get_system_prompt_block(agent_id=self.agent_id)

    def sync(self, user_msg: str, assistant_msg: str) -> None:
        """Post-turn sync — extract memorable facts via LLM.

        Runs asynchronously in a daemon thread to avoid blocking the conversation.
        Only extracts durable facts (preferences, instructions, key info).
        """
        # Skip trivial turns (greetings, very short messages)
        if len(user_msg.strip()) < 20:
            return

        def _extract():
            try:
                from my_agent_memory.llm import (
                    LLMClient, build_extract_messages, parse_extract_response,
                )

                llm = LLMClient()
                messages = build_extract_messages(user_msg, assistant_msg)
                response = llm.chat(messages, temperature=0.1, max_tokens=300)

                if not response:
                    return

                result = parse_extract_response(response)
                if not result:
                    return

                self.store.save(
                    content=result["content"],
                    title=result["title"],
                    tags=result.get("tags", []),
                    source="auto_extract",
                )
                logger.info("Auto-extracted memory: %s", result["title"][:60])

            except Exception as e:
                logger.debug("Memory extraction failed (non-critical): %s", e)

        t = threading.Thread(target=_extract, daemon=True)
        t.start()

    def on_session_end(self) -> None:
        """Session end — optionally trigger dreaming.

        Dreams are typically managed by a separate cron, so this is optional.
        """
        pass
