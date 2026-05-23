"""Tests for hot_layer module."""

from unittest.mock import MagicMock
from my_agent_memory.hot_layer import HotLayer


class TestFormatMemoryMd:
    def setup_method(self):
        self.db = MagicMock()
        self.hl = HotLayer(self.db, hermes_home="/tmp/test-hermes")

    def test_no_duplicate_entries(self):
        """Non-pinned entries should appear exactly once (regression for duplicate bug)."""
        entries = [
            {"id": 1, "title": "Test", "content": "Hello", "is_pinned": 0, "scope": "private", "owner_agent": "noor"},
        ]
        result = self.hl._format_memory_md("noor", entries)
        assert result.count("**Test**") == 1

    def test_pinned_before_active(self):
        entries = [
            {"id": 1, "title": "Active", "content": "A", "is_pinned": 0, "scope": "private", "owner_agent": "noor"},
            {"id": 2, "title": "Pinned", "content": "B", "is_pinned": 1, "scope": "private", "owner_agent": "noor"},
        ]
        result = self.hl._format_memory_md("noor", entries)
        pinned_pos = result.index("Pinned")
        active_pos = result.index("Active")
        assert pinned_pos < active_pos

    def test_shared_header(self):
        result = self.hl._format_memory_md("shared", [])
        assert "Shared Memory" in result

    def test_agent_header(self):
        result = self.hl._format_memory_md("noor", [])
        assert "noor" in result


class TestGetSystemPromptBlock:
    def setup_method(self):
        self.db = MagicMock()
        self.hl = HotLayer(self.db, hermes_home="/tmp/test-hermes")

    def test_empty_entries(self):
        self.db.fetchall.return_value = []
        result = self.hl.get_system_prompt_block("noor")
        assert result == ""

    def test_max_chars_truncation(self):
        entries = [
            {"id": i, "title": f"Title {i}", "content": "x" * 100,
             "is_pinned": 0, "scope": "private", "owner_agent": "noor",
             "state": "promoted", "deleted_at": None}
            for i in range(50)
        ]
        self.db.fetchall.return_value = entries
        result = self.hl.get_system_prompt_block("noor", max_chars=500)
        assert len(result) <= 500
