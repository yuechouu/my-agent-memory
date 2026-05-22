# Hermes Agent 记忆架构（实际实现状态）

> 2026-05-22 · 基于代码审计，非文档

---

## 三层记忆模型 ✅

```
┌─ Hot 层 ──────────────────────────────────────────┐
│ 系统 prompt 尾部注入                                │
│ 来源：$HERMES_HOME/memories/ 下 Markdown 文件        │
│ 内容：USER.md(用户画像) + MEMORY.md(环境笔记)          │
│ 每次对话自动加载，无需搜索                            │
└──────────────────────┬─────────────────────────────┘
                       │ dreaming promote (✅)
┌─ Warm 层 ──────────────────────────────────────────┐
│ $HERMES_HOME/memories/memory.db                    │
│ 表：memory_entries (33条) + FTS5 索引               │
│ 表：dreaming_log (1条)                              │
│ 搜索：FTS5 BM25 + 中文 LIKE fallback                 │
│ API：store.search() / store.save()                  │
└──────────────────────┬─────────────────────────────┘
                       │ dreaming archive (✅)
┌─ Cold 层 ──────────────────────────────────────────┐
│ $HERMES_HOME/state.db (SQLite FTS5)                │
│ 存储所有历史 session 完整消息                        │
│ API：session_search("关键词")                        │
└────────────────────────────────────────────────────┘
```

---

## Dreaming 引擎 ✅

```
memory_save() → Warm (SQLite)
    │
    ├─ access_count >= 2 → promote → Hot (MEMORY.md)    ✅
    │
    └─ 180天未访问 → archive → SQLite 软删除             ✅

调度：每 6h cron（间隔计时）
位置：cron job memory-dreaming (id: f14d106ed7ce)
```

**未实现的功能：**

| 功能 | 状态 | 说明 |
|------|------|------|
| demote（降级） | ❌ | 代码中不存在 |
| purge（硬删） | ❌ | 代码中不存在 |
| 时间衰减评分 | ❌ | 只用 access_count >= 2 |

---

## 向量搜索 ❌ 未实施

```
WARM LAYER  ┌──────────────┐
            │ FTS5 (base)  │     ← 只有这个
            │ 始终可用      │
            └──────────────┘

v2 设计的 RRF 融合搜索、SiliconFlow Embedding、memory_vectors 表 —— 全部未写代码。
当前只有 v1 的 FTS5 纯文本搜索。
```

---

## 实际代码结构

```
hermes-memory/src/hermes_memory/
├── __init__.py     Store 导出
├── cli.py          CLI + Store API (search/save/get/status/dream/rebuild/archive)
├── db.py           SQLite + FTS5 (无 vector/embedding 代码)
└── dreaming.py     promote + archive (无 demote/purge/decay)
```

---

## 存储位置

| 层 | 位置 | 格式 | 条目 |
|----|------|------|------|
| Hot | `$HERMES_HOME/memories/*.md` | Markdown | 6个文件 |
| Warm | `$HERMES_HOME/memories/memory.db` | SQLite + FTS5 | 33条 |
| Cold | `$HERMES_HOME/state.db` | SQLite + FTS5 | 237 sessions |
