"""Tests for memory type configuration."""

import pytest
from my_agent_memory.memory_types import (
    VALID_MEMORY_TYPES, MEMORY_TYPE_CONFIG, DEFAULT_MEMORY_TYPE,
    get_type_config, is_valid_type, LEGACY_TYPE_MAP,
)


class TestMemoryTypeConfig:
    def test_all_types_have_required_keys(self):
        required_keys = {"label", "half_life_days", "promote_threshold",
                         "demote_threshold", "archive_threshold",
                         "conflict_strategy", "hot_layer_order", "emoji"}
        for type_key in VALID_MEMORY_TYPES:
            cfg = MEMORY_TYPE_CONFIG[type_key]
            missing = required_keys - set(cfg.keys())
            assert not missing, f"{type_key} missing keys: {missing}"

    def test_valid_types(self):
        """Test that all expected types are present."""
        expected_types = {
            "user-identity", "user-preference", "user-context",
            "feedback-correction", "feedback-confirmation", "feedback-preference",
            "project-progress", "project-goal", "project-decision", "project-issue",
            "learned-research", "learned-solution", "learned-summary", "learned-pattern",
            "knowledge-research", "knowledge-solution", "knowledge-summary", "knowledge-pattern",
            "reference-url", "reference-doc", "reference-code", "reference-config",
            "knowledge-domain",
        }
        assert set(VALID_MEMORY_TYPES) == expected_types

    def test_default_type_is_knowledge_summary(self):
        assert DEFAULT_MEMORY_TYPE == "knowledge-summary"

    def test_legacy_types_mapped(self):
        """Test that legacy types are mapped correctly."""
        assert LEGACY_TYPE_MAP["procedural"] == "learned-solution"
        assert LEGACY_TYPE_MAP["entity"] == "user-identity"
        assert LEGACY_TYPE_MAP["knowledge"] == "knowledge-summary"

    def test_procedural_no_decay(self):
        cfg = get_type_config("procedural")
        assert cfg["half_life_days"] is None

    def test_knowledge_no_decay(self):
        cfg = get_type_config("knowledge-summary")
        assert cfg["half_life_days"] is None

    def test_user_context_has_decay(self):
        cfg = get_type_config("user-context")
        assert cfg["half_life_days"] == 7

    def test_feedback_correction_promotes_easiest(self):
        assert get_type_config("feedback-correction")["promote_threshold"] < get_type_config("user-identity")["promote_threshold"]

    def test_project_decision_promotes_hardest(self):
        # project-decision has highest promote threshold among types that have one
        assert get_type_config("project-decision")["promote_threshold"] == 3.0

    def test_knowledge_has_no_promote_threshold(self):
        # knowledge types are already at the highest level
        assert get_type_config("knowledge-summary")["promote_threshold"] is None

    def test_is_valid_type(self):
        assert is_valid_type("user-identity") is True
        assert is_valid_type("feedback-correction") is True
        assert is_valid_type("learned-solution") is True
        assert is_valid_type("knowledge-summary") is True
        # Legacy types are also valid
        assert is_valid_type("procedural") is True
        assert is_valid_type("entity") is True
        assert is_valid_type("knowledge") is True
        assert is_valid_type("unknown") is False
        assert is_valid_type("") is False

    def test_get_type_config_fallback(self):
        cfg = get_type_config("unknown_type")
        default = get_type_config("knowledge-summary")
        assert cfg == default
