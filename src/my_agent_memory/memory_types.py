"""Memory type configuration — lifecycle parameters per type.

Three types:
  - procedural: workflows, how-to steps, instructions (不衰减)
  - knowledge: general facts, concepts, configurations (不衰减)
  - entity: facts about specific things that change over time (30天半衰期)
"""

__all__ = [
    "VALID_MEMORY_TYPES", "MEMORY_TYPE_CONFIG", "DEFAULT_MEMORY_TYPE",
    "get_type_config", "is_valid_type",
]

VALID_MEMORY_TYPES = ("procedural", "entity", "knowledge")

DEFAULT_MEMORY_TYPE = "knowledge"

MEMORY_TYPE_CONFIG = {
    "procedural": {
        "label": "流程性",
        "half_life_days": None,           # None = no time decay
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "keep_both",
        "hot_layer_order": 1,
        "emoji": "⚙️",          # ⚙️
    },
    "knowledge": {
        "label": "知识性",
        "half_life_days": None,           # None = no time decay
        "promote_threshold": 2.5,
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 2,
        "emoji": "\U0001f4da",            # 📚
    },
    "entity": {
        "label": "实体性",
        "half_life_days": 30,
        "promote_threshold": 3.0,
        "demote_threshold": 1.0,
        "archive_threshold": 0.1,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 3,
        "emoji": "\U0001f3f7️",      # 🏷️
    },
}


def get_type_config(memory_type: str) -> dict:
    """Get configuration for a memory type. Falls back to default if unknown."""
    return MEMORY_TYPE_CONFIG.get(memory_type, MEMORY_TYPE_CONFIG[DEFAULT_MEMORY_TYPE])


def is_valid_type(memory_type: str) -> bool:
    return memory_type in VALID_MEMORY_TYPES
