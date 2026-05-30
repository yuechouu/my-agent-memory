"""Memory type configuration — lifecycle parameters per type.

Memory Type Hierarchy:
  - user-*:       用户身份、偏好、上下文
  - feedback-*:   纠正、确认、偏好反馈
  - project-*:    进展、目标、决策、问题
  - learned-*:    agent 自学习（调研、解决方案、总结、模式）
  - knowledge-*:  晋升后的知识（从 learned-* 晋升）
  - reference-*:  外部引用（URL、文档、代码、配置）
  - knowledge-domain: 领域知识（数学、哲学、编程等）
"""

__all__ = [
    "VALID_MEMORY_TYPES", "MEMORY_TYPE_CONFIG", "DEFAULT_MEMORY_TYPE",
    "get_type_config", "is_valid_type", "LEGACY_TYPE_MAP",
]

# Legacy type mapping for backward compatibility
LEGACY_TYPE_MAP = {
    "procedural": "learned-solution",
    "entity": "user-identity",
    "knowledge": "knowledge-summary",
}

VALID_MEMORY_TYPES = (
    # === 人物 ===
    "user-identity",        # 身份、角色、技能水平
    "user-preference",      # 偏好、习惯
    "user-context",         # 当前状态、环境（会衰减）

    # === 反馈 ===
    "feedback-correction",  # 纠正（"不是这样"）
    "feedback-confirmation", # 确认（"对，就这样"）
    "feedback-preference",  # 偏好（"我喜欢..."）

    # === 项目 ===
    "project-progress",     # 进展记录
    "project-goal",         # 目标、计划
    "project-decision",     # 决策、选型
    "project-issue",        # 问题、bug

    # === 学习 ===
    "learned-research",     # 调研结果
    "learned-solution",     # 问题解决方案
    "learned-summary",      # 主题总结
    "learned-pattern",      # 模式、最佳实践

    # === 知识（晋升目标）===
    "knowledge-research",   # 从 learned-research 晋升
    "knowledge-solution",   # 从 learned-solution 晋升
    "knowledge-summary",    # 从 learned-summary 晋升
    "knowledge-pattern",    # 从 learned-pattern 晋升

    # === 引用 ===
    "reference-url",        # URL 链接
    "reference-doc",        # 文档
    "reference-code",       # 代码片段
    "reference-config",     # 配置

    # === 领域知识 ===
    "knowledge-domain",     # 带 domain 标签的领域知识
)

DEFAULT_MEMORY_TYPE = "knowledge-summary"

MEMORY_TYPE_CONFIG = {
    # === 人物 ===
    "user-identity": {
        "label": "身份",
        "half_life_days": None,
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 1,
        "emoji": "👤",
    },
    "user-preference": {
        "label": "偏好",
        "half_life_days": None,
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 2,
        "emoji": "⭐",
    },
    "user-context": {
        "label": "上下文",
        "half_life_days": 7,           # 上下文信息衰减快
        "promote_threshold": 1.5,
        "demote_threshold": 0.3,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 3,
        "emoji": "📌",
    },

    # === 反馈 ===
    "feedback-correction": {
        "label": "纠正",
        "half_life_days": None,
        "promote_threshold": 1.0,      # 纠正立即生效
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "keep_both",
        "hot_layer_order": 4,
        "emoji": "🔧",
    },
    "feedback-confirmation": {
        "label": "确认",
        "half_life_days": None,
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 5,
        "emoji": "✅",
    },
    "feedback-preference": {
        "label": "偏好反馈",
        "half_life_days": None,
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 6,
        "emoji": "💡",
    },

    # === 项目 ===
    "project-progress": {
        "label": "进展",
        "half_life_days": 30,
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 7,
        "emoji": "📊",
    },
    "project-goal": {
        "label": "目标",
        "half_life_days": None,
        "promote_threshold": 2.5,
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 8,
        "emoji": "🎯",
    },
    "project-decision": {
        "label": "决策",
        "half_life_days": None,
        "promote_threshold": 3.0,      # 决策需要更多确认
        "demote_threshold": 1.0,
        "archive_threshold": 0.1,
        "conflict_strategy": "keep_both",
        "hot_layer_order": 9,
        "emoji": "⚖️",
    },
    "project-issue": {
        "label": "问题",
        "half_life_days": 14,          # issue 解决后衰减
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 10,
        "emoji": "🐛",
    },

    # === 学习 ===
    "learned-research": {
        "label": "调研",
        "half_life_days": None,
        "promote_threshold": 3.0,      # 调研需要多次验证
        "demote_threshold": 1.0,
        "archive_threshold": 0.1,
        "conflict_strategy": "merge",
        "hot_layer_order": 11,
        "emoji": "🔍",
        "promote_to": "knowledge-research",
    },
    "learned-solution": {
        "label": "方案",
        "half_life_days": None,
        "promote_threshold": 2.0,      # 解决方案较快晋升
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 12,
        "emoji": "💡",
        "promote_to": "knowledge-solution",
    },
    "learned-summary": {
        "label": "总结",
        "half_life_days": None,
        "promote_threshold": 3.0,
        "demote_threshold": 1.0,
        "archive_threshold": 0.1,
        "conflict_strategy": "merge",
        "hot_layer_order": 13,
        "emoji": "📝",
        "promote_to": "knowledge-summary",
    },
    "learned-pattern": {
        "label": "模式",
        "half_life_days": None,
        "promote_threshold": 2.5,
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 14,
        "emoji": "🧩",
        "promote_to": "knowledge-pattern",
    },

    # === 知识（晋升目标）===
    "knowledge-research": {
        "label": "调研知识",
        "half_life_days": None,
        "promote_threshold": None,     # 已是最高级
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 15,
        "emoji": "🔬",
    },
    "knowledge-solution": {
        "label": "方案知识",
        "half_life_days": None,
        "promote_threshold": None,
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 16,
        "emoji": "🛠️",
    },
    "knowledge-summary": {
        "label": "知识",
        "half_life_days": None,
        "promote_threshold": None,
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 17,
        "emoji": "📚",
    },
    "knowledge-pattern": {
        "label": "模式知识",
        "half_life_days": None,
        "promote_threshold": None,
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 18,
        "emoji": "🏗️",
    },

    # === 引用 ===
    "reference-url": {
        "label": "链接",
        "half_life_days": 90,
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 19,
        "emoji": "🔗",
    },
    "reference-doc": {
        "label": "文档",
        "half_life_days": None,
        "promote_threshold": 2.5,
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 20,
        "emoji": "📄",
    },
    "reference-code": {
        "label": "代码",
        "half_life_days": None,
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 21,
        "emoji": "💻",
    },
    "reference-config": {
        "label": "配置",
        "half_life_days": None,
        "promote_threshold": 2.0,
        "demote_threshold": 0.5,
        "archive_threshold": 0.05,
        "conflict_strategy": "last_write_wins",
        "hot_layer_order": 22,
        "emoji": "⚙️",
    },

    # === 领域知识 ===
    "knowledge-domain": {
        "label": "领域",
        "half_life_days": None,
        "promote_threshold": 2.5,
        "demote_threshold": 0.8,
        "archive_threshold": 0.05,
        "conflict_strategy": "merge",
        "hot_layer_order": 23,
        "emoji": "🎓",
    },
}


def get_type_config(memory_type: str) -> dict:
    """Get configuration for a memory type. Falls back to default if unknown."""
    # Try direct match
    if memory_type in MEMORY_TYPE_CONFIG:
        return MEMORY_TYPE_CONFIG[memory_type]
    # Try legacy mapping
    mapped = LEGACY_TYPE_MAP.get(memory_type)
    if mapped and mapped in MEMORY_TYPE_CONFIG:
        return MEMORY_TYPE_CONFIG[mapped]
    return MEMORY_TYPE_CONFIG[DEFAULT_MEMORY_TYPE]


def is_valid_type(memory_type: str) -> bool:
    """Check if a memory type is valid (including legacy types)."""
    return memory_type in VALID_MEMORY_TYPES or memory_type in LEGACY_TYPE_MAP


def normalize_type(memory_type: str) -> str:
    """Normalize a memory type, mapping legacy types to new ones."""
    return LEGACY_TYPE_MAP.get(memory_type, memory_type)
