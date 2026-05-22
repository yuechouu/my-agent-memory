"""Hermes memory — compatibility shim.
Redirects `from hermes_memory import Store` to v2 my_agent_memory.
No hermes-agent core code changes needed.
"""
from my_agent_memory.store import MultiAgentStore, Store

__all__ = ["Store", "MultiAgentStore"]
