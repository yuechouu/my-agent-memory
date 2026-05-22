"""Hot layer — deterministic Markdown projection of SQLite state.

Generates per-agent Markdown files from the SQLite entries. Writing uses
temp → fsync → rename for atomicity (compatible with hanako's frozen snapshot).

Structure:
  {hermes_home}/memories/
    shared/MEMORY.md      — scope=shared entries
    {agent}/MEMORY.md     — agent's promoted/hot entries
    {agent}/USER.md       — user profile (preserved from v1, not generated)
"""

import os
from datetime import datetime
from pathlib import Path


def _get_hermes_home() -> Path:
    hermes_home = os.getenv("HERMES_HOME", "")
    if hermes_home:
        return Path(hermes_home)
    return Path.home() / ".hermes"


class HotLayer:
    """Generates and maintains the hot layer Markdown projection."""

    def __init__(self, db, hermes_home: str = ""):
        self.db = db
        self.hermes_home = Path(hermes_home) if hermes_home else _get_hermes_home()
        self.mem_dir = self.hermes_home / "memories"

    def rebuild_all(self):
        """Rebuild hot layer for all agents with entries."""
        # Rebuild shared layer
        self._rebuild_agent("shared")

        # Rebuild per-agent layers
        agents = self._get_known_agents()
        for agent_id in agents:
            if agent_id != "shared":
                self._rebuild_agent(agent_id)

    def _get_known_agents(self) -> list[str]:
        rows = self.db.fetchall(
            "SELECT DISTINCT owner_agent FROM memory_entries WHERE deleted_at IS NULL"
        )
        return [r["owner_agent"] for r in rows]

    def rebuild_agent(self, agent_id: str):
        """Rebuild hot layer for a specific agent."""
        self._rebuild_agent(agent_id)

    def _rebuild_agent(self, agent_id: str):
        """Generate MEMORY.md for one agent (or shared layer)."""
        dir_path = self.mem_dir / agent_id
        dir_path.mkdir(parents=True, exist_ok=True)

        entries = self._get_entries_for_agent(agent_id)
        content = self._format_memory_md(agent_id, entries)

        file_path = dir_path / "MEMORY.md"
        self._atomic_write(file_path, content)

    def _get_entries_for_agent(self, agent_id: str) -> list[dict]:
        """Get hot layer entries for an agent.

        For 'shared': all scope=shared entries.
        For specific agents: their promoted/hot entries + shared entries.
        """
        if agent_id == "shared":
            rows = self.db.fetchall("""
                SELECT * FROM memory_entries
                WHERE scope = 'shared'
                  AND state IN ('promoted', 'hot')
                  AND deleted_at IS NULL
                ORDER BY is_pinned DESC, score DESC
            """)
            return rows

        rows = self.db.fetchall("""
            SELECT * FROM memory_entries
            WHERE (owner_agent = ? OR scope = 'shared')
              AND state IN ('promoted', 'hot')
              AND deleted_at IS NULL
            ORDER BY is_pinned DESC, score DESC
        """, (agent_id,))
        return rows

    def get_system_prompt_block(self, agent_id: str, max_chars: int = None) -> str:
        """Get the hot layer content formatted for system prompt injection.

        Agent-specific entries + shared entries, sorted by score.
        Consumer is responsible for truncation to its own token budget.

        Args:
            agent_id: Agent to get hot layer for.
            max_chars: Optional max characters. If set, truncates from the bottom
                       (lowest-scored entries removed first) to fit.

        Returns:
            Markdown-formatted string for system prompt injection.
        """
        entries = self._get_entries_for_agent(agent_id)
        if not entries:
            return ""

        lines = [f"## Memory ({agent_id})", ""]

        # Separate pinned and regular entries (pinned first)
        pinned = [e for e in entries if e.get("is_pinned")]
        regular = [e for e in entries if not e.get("is_pinned")]

        if pinned:
            lines.append("### Pinned")
            lines.append("")
            for e in pinned:
                lines.append(self._format_entry(e))
                lines.append("")

        if regular:
            lines.append("### Active")
            lines.append("")
            for e in regular:
                lines.append(self._format_entry(e))
                lines.append("")

        result = "\n".join(lines)

        if max_chars and len(result) > max_chars:
            # Truncate from bottom — keep the highest-scored entries
            truncated = result[:max_chars]
            last_newline = truncated.rfind("\n\n")
            if last_newline > 0:
                truncated = truncated[:last_newline]
            return truncated + "\n"

        return result

    def _format_entry(self, entry: dict) -> str:
        """Format a single entry for the hot layer."""
        from hermes_memory_v2.db import _enrich_row
        e = _enrich_row(entry) if not isinstance(entry.get("tags"), list) else entry

        pin_marker = "📌 " if e.get("is_pinned") else ""
        scope_marker = ""
        if e.get("scope") == "shared":
            scope_marker = f" [{e.get('owner_agent', '')}]"

        title = e.get("title", "") or "(untitled)"
        content = e.get("content", "")
        # Truncate per-entry content for hot layer (long content belongs in SQLite)
        if len(content) > 200:
            content = content[:200] + "..."

        return f"- {pin_marker}**{title}**{scope_marker}: {content}"

    def _format_memory_md(self, agent_id: str, entries: list[dict]) -> str:
        """Format the full MEMORY.md file content."""
        from hermes_memory_v2.db import _enrich_row

        updated = datetime.now().strftime("%Y-%m-%d %H:%M")
        if agent_id == "shared":
            header = f"# Shared Memory\n*{len(entries)} entries · updated {updated}*\n"
        else:
            header = f"# Memory — {agent_id}\n*{len(entries)} entries · updated {updated}*\n"

        lines = [header, ""]

        # Pinned section
        pinned = [e for e in entries if e.get("is_pinned")]
        if pinned:
            lines.append("## Pinned")
            for e in pinned:
                e = _enrich_row(e) if not isinstance(e.get("tags"), list) else e
                lines.append(f"- **{e.get('title', '(untitled)')}**: {e.get('content', '')}")
            lines.append("")

        # Active section
        regular = [e for e in entries if not e.get("is_pinned")]
        if regular:
            lines.append("## Active")
            for e in regular:
                e = _enrich_row(e) if not isinstance(e.get("tags"), list) else e
                lines.append(f"- **{e.get('title', '(untitled)')}**: {e.get('content', '')}")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _atomic_write(file_path: Path, content: str):
        """Write content atomically: temp file → fsync → rename.

        Ensures the file is never left in a partially-written state.
        Hanako-style frozen snapshot readers always see a complete file.
        """
        temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        temp_path.write_text(content, encoding="utf-8")

        # Force flush to disk
        with open(temp_path, "r+b") as f:
            os.fsync(f.fileno())

        # Atomic rename
        temp_path.replace(file_path)
