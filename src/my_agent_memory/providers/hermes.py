"""Hermes v2 memory provider — my-agent-memory with Hermes MemoryProvider interface.

Wraps MultiAgentStore as a proper Hermes MemoryProvider plugin.
Three-layer automation: tool descriptions, system prompt guidelines, event fallbacks.

Config in $HERMES_HOME/config.yaml:
  plugins:
    hermes-v2:
      db_path: $HERMES_HOME/memories/memory_v2.db
      agent_id: hermes
"""

from my_agent_memory.providers.base import MemoryProviderBase, _load_plugin_config


class HermesV2Provider(MemoryProviderBase):
    """Hermes MemoryProvider backed by my-agent-memory MultiAgentStore."""

    def __init__(self, config: dict | None = None):
        super().__init__(
            name="hermes-v2",
            agent_id="hermes",
            config_key="hermes-v2",
            config=config or _load_plugin_config("hermes-v2"),
        )


def register(ctx):
    """Called by hermes-agent's plugin loader. ctx has register_memory_provider()."""
    from agent.memory_provider import MemoryProvider

    class _HermesProvider(HermesV2Provider, MemoryProvider):
        pass

    ctx.register_memory_provider(_HermesProvider())
