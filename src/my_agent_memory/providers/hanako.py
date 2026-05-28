"""Hanako memory provider — my-agent-memory with Hanako MemoryProvider interface.

Wraps MultiAgentStore as a Hanako MemoryProvider plugin.
Same three-layer automation as Hermes, but defaults to agent_id=hanako.

Config in $HERMES_HOME/config.yaml:
  plugins:
    hanako-v2:
      db_path: $HERMES_HOME/memories/memory_v2.db
      agent_id: hanako
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from my_agent_memory.providers.hermes import (
    HermesV2Provider,
    MEMORY_GUIDELINES,
    SAVE_KEYWORDS,
    MEMORY_KEYWORDS,
)

logger = logging.getLogger(__name__)


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
        return cfg_get(all_config, "plugins", "hanako-v2", default={}) or {}
    except Exception:
        return {}


class HanakoV2Provider(HermesV2Provider):
    """Hanako MemoryProvider backed by my-agent-memory MultiAgentStore.

    Inherits all tool schemas, lifecycle hooks, and three-layer automation
    from HermesV2Provider. Only overrides config loading and default agent_id.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config or _load_plugin_config())

    @property
    def name(self) -> str:
        return "hanako-v2"

    def initialize(self, session_id: str, **kwargs) -> None:
        from my_agent_memory.store import MultiAgentStore

        hermes_home = kwargs.get("hermes_home") or os.getenv("HERMES_HOME", "")
        db_path = self._config.get("db_path", "")
        agent_id = self._config.get("agent_id", "hanako")

        if db_path and hermes_home:
            db_path = db_path.replace("$HERMES_HOME", hermes_home).replace("${HERMES_HOME}", hermes_home)

        self._store = MultiAgentStore(
            db_path=db_path,
            agent_id=agent_id,
            hermes_home=hermes_home,
        )
        self._session_id = session_id
        logger.info("Hanako v2 memory provider initialized (agent=%s)", agent_id)

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
            existing["plugins"]["hanako-v2"] = values
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception:
            pass

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "db_path", "description": "SQLite database path", "default": "$HERMES_HOME/memories/memory_v2.db"},
            {"key": "agent_id", "description": "Agent identifier", "default": "hanako"},
        ]


def register(ctx):
    """Called by hermes-agent's plugin loader. ctx has register_memory_provider()."""
    from agent.memory_provider import MemoryProvider

    class _HanakoProvider(HanakoV2Provider, MemoryProvider):
        pass

    ctx.register_memory_provider(_HanakoProvider())
