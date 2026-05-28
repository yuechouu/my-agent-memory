"""Hanako memory provider — my-agent-memory with Hanako MemoryProvider interface.

Wraps MultiAgentStore as a Hanako MemoryProvider plugin.
Same three-layer automation as Hermes, with agent_id=hanako.

Config in $HERMES_HOME/config.yaml:
  plugins:
    hanako-v2:
      db_path: $HERMES_HOME/memories/memory_v2.db
      agent_id: hanako
"""

from my_agent_memory.providers.base import MemoryProviderBase, _load_plugin_config


class HanakoV2Provider(MemoryProviderBase):
    """Hanako MemoryProvider backed by my-agent-memory MultiAgentStore."""

    def __init__(self, config: dict | None = None):
        super().__init__(
            name="hanako-v2",
            agent_id="hanako",
            config_key="hanako-v2",
            config=config or _load_plugin_config("hanako-v2"),
        )


def register(ctx):
    """Called by hermes-agent's plugin loader. ctx has register_memory_provider()."""
    from agent.memory_provider import MemoryProvider

    class _HanakoProvider(HanakoV2Provider, MemoryProvider):
        pass

    ctx.register_memory_provider(_HanakoProvider())
