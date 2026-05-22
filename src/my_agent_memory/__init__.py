"""My Agent Memory — Multi-agent shared memory system.

SQLite + FTS5 + sqlite-vec vector search with per-agent namespace isolation.

Usage:
    from my_agent_memory import MultiAgentStore

    store = MultiAgentStore(agent_id="noor")
    store.save("fact", title="Title")
    results = store.search("query")

For v1 compatibility:
    from my_agent_memory import Store  # Same API as v1

CLI:
    my-agent-memory search <query>
    my-agent-memory hybrid <query>
    my-agent-memory save <content>
    my-agent-memory serve --port 8765
"""

from my_agent_memory.store import MultiAgentStore, Store

__all__ = ["MultiAgentStore", "Store"]
__version__ = "2.0.0"
