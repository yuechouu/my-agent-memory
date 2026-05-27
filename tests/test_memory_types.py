"""Tests for memory type configuration."""

import pytest
from my_agent_memory.memory_types import (
    VALID_MEMORY_TYPES, MEMORY_TYPE_CONFIG, DEFAULT_MEMORY_TYPE,
    get_type_config, is_valid_type,
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
        assert set(VALID_MEMORY_TYPES) == {"procedural", "entity", "knowledge"}

    def test_default_type_is_knowledge(self):
        assert DEFAULT_MEMORY_TYPE == "knowledge"

    def test_procedural_no_decay(self):
        cfg = get_type_config("procedural")
        assert cfg["half_life_days"] is None

    def test_knowledge_no_decay(self):
        cfg = get_type_config("knowledge")
        assert cfg["half_life_days"] is None

    def test_entity_has_decay(self):
        cfg = get_type_config("entity")
        assert cfg["half_life_days"] == 30

    def test_procedural_promotes_easiest(self):
        assert get_type_config("procedural")["promote_threshold"] < get_type_config("entity")["promote_threshold"]

    def test_entity_promotes_hardest(self):
        assert get_type_config("entity")["promote_threshold"] > get_type_config("knowledge")["promote_threshold"]

    def test_is_valid_type(self):
        assert is_valid_type("procedural") is True
        assert is_valid_type("entity") is True
        assert is_valid_type("knowledge") is True
        assert is_valid_type("unknown") is False
        assert is_valid_type("") is False

    def test_get_type_config_fallback(self):
        cfg = get_type_config("unknown_type")
        default = get_type_config("knowledge")
        assert cfg == default
