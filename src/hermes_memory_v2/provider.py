"""MemoryProvider ABC — external memory backend interface.

This ABC defines the contract for pluggable memory providers in Hanako's
MemoryManager. v2 ships HanakoProvider as the first implementation.

The ABC is intended to be copied into Hanako's agent/memory_provider.py
as part of the MemoryManager extension point setup.
"""

from abc import ABC, abstractmethod
from typing import Optional


class MemoryProvider(ABC):
    """Abstract base class for external memory backends.

    Hanako's MemoryManager loads one external provider at startup.
    The provider is called at specific lifecycle points:
      - System prompt assembly: system_prompt_block()
      - Pre-turn recall: prefetch(query)
      - Post-turn persistence: sync(user_msg, assistant_msg)
      - Session end: on_session_end()
    """

    @abstractmethod
    def prefetch(self, query: str) -> str:
        """Called at the start of each turn.

        Args:
            query: The user's message for this turn.

        Returns:
            Recalled memory content as a string, to be appended to the
            message context (not injected into the system prompt).
            Return empty string if no relevant memories found.
        """
        ...

    @abstractmethod
    def system_prompt_block(self) -> str:
        """Called during system prompt assembly.

        Returns:
            Text block to be injected into the system prompt's volatile layer,
            after the builtin frozen snapshot block.
        """
        ...

    @abstractmethod
    def sync(self, user_msg: str, assistant_msg: str) -> None:
        """Called at the end of each turn.

        Args:
            user_msg: The user's message for this turn.
            assistant_msg: The assistant's response for this turn.
        """
        ...

    @abstractmethod
    def on_session_end(self) -> None:
        """Called when the session ends (explicit exit or timeout).

        Use for final persistence, memory extraction, or summarization.
        """
        ...
