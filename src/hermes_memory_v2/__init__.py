"""Hermes Memory v2 — Multi-agent shared memory system.

SQLite + FTS5 + sqlite-vec vector search with per-agent namespace isolation.

Usage:
    from hermes_memory_v2 import MultiAgentStore

    store = MultiAgentStore(agent_id="noor")
    store.save("fact", title="Title")
    results = store.search("query")

For v1 compatibility:
    from hermes_memory_v2 import Store  # Same API as v1

CLI:
    hermes-memory search <query>
    hermes-memory hybrid <query>
    hermes-memory save <content>
    hermes-memory serve --port 8765
"""

from hermes_memory_v2.store import MultiAgentStore, Store

__all__ = ["MultiAgentStore", "Store"]
__version__ = "2.0.0"
