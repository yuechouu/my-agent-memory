# Hanako 记忆架构分析

## 结构概览

```
系统提示词（三层注入）
  ├─ stable 层（人格、规则、指南）
  ├─ context 层（工作区上下文文件）
  └─ volatile 层 ← 记忆注入在这里
       ├─ MEMORY.md 快照（agent 笔记）
       ├─ USER.md 快照（用户画像）
       └─ 外部 provider 文本块（如 Supermemory）

工具层
  ├─ memory 工具 → MemoryStore.add/replace/remove
  └─ 外部 provider 工具（由插件注册）

存储层
  ├─ {HERMES_HOME}/memories/MEMORY.md（§ 分隔文本文件）
  ├─ {HERMES_HOME}/memories/USER.md
  └─ 外部 provider 自己的后端（SQLite、向量库、云服务）
```

---

## 第一层：存储 — MemoryStore

### 两个文件，一个分隔符

- `MEMORY.md` — agent 的环境知识、工程习惯、工具怪癖
- `USER.md` — 用户的身份、偏好、沟通风格

每条记录用 `§` 分隔，字符上限分别是 2200 / 1375 字符。

### 关键设计

**原子写入**。不直接在原文件上 `open("w")`，而是写临时文件 → `os.fsync` → `os.replace` 原子 rename。读端不加锁，因为始终读到的是完整旧文件或完整新文件。并发写入走 `.lock` 文件 + `msvcrt.locking`（Windows）做互斥。

**注入扫描**。内容写入前检查隐形 Unicode 字符（零宽空格、BOM、方向控制符）和注入模式（`ignore previous instructions`、`curl ... $API_KEY` 等），命中直接拒绝。

---

## 第二层：Frozen Snapshot 模式

MemoryStore 维护两套状态：

| 状态 | 来源 | 生命周期 |
|---|---|---|
| 内存条目列表（`memory_entries`） | 磁盘 + 工具写入 | 整个 session |
| 系统提示词快照（`_system_prompt_snapshot`） | 仅 session 启动时从磁盘加载 | 冻结，永不变更 |

Session 中调 `memory add` 会立即写磁盘，但系统提示词里的记忆块不刷新。这是为了保持 prefix cache 稳定——系统提示词如果是恒定的，上游 API 可以缓存 KV，每轮只需计算增量。

快照在下次 session 启动时重新加载，届时磁盘上的最新状态才会进入系统提示词。

---

## 第三层：MemoryManager — 编排层

位于 `agent/memory_manager.py`，是 `run_agent.py` 的唯一集成点。

### 注册规则

一个 builtin（"builtin"）+ 至多一个外部 provider。第二个外部 provider 尝试注册会被拒绝并 warning。限制原因是防止工具 schema 膨胀和记忆后端冲突。

### 生命周期调度

| 时机 | 调用 | 说明 |
|---|---|---|
| Session 启动 | `initialize_all(session_id)` | 传入 hermes_home、platform、agent_context 等 |
| 每轮开始 | `prefetch_all(query)` | 合并所有 provider 的回忆文本 |
| 每轮开始 | `build_system_prompt()` | 收集 system_prompt_block() |
| 每轮结束 | `sync_all(user, asst)` | 写入所有 provider |
| 每轮结束 | `queue_prefetch_all()` | 为下一轮安排后台 prefetch |
| Session 结束 | `on_session_end()` | 提取、总结 |
| 退出 | `shutdown_all()` | 逆序关闭 |

### 可选 Hook 回调

| Hook | 触发时机 | 典型用途 |
|---|---|---|
| `on_turn_start` | 每轮开始 | 计数、范围管理 |
| `on_session_switch` | /resume, /branch, /reset, 压缩 | 刷新 session 级缓存 |
| `on_pre_compress` | 上下文压缩前 | 从即将丢弃的消息中提取洞察 |
| `on_session_end` | 显式退出或超时 | 事实提取、摘要 |
| `on_delegation` | 子代理完成 | 父代理观察子代理工作 |
| `on_memory_write` | builtin memory 工具写入后 | 镜像写入外部后端 |

---

## 第四层：注入到系统提示词

系统提示词分为三层，记忆在 **volatile** 层：

```
volatile_parts:
  ├─ format_for_system_prompt("memory")   ← 冻结快照（如果 memory_enabled）
  ├─ format_for_system_prompt("user")     ← 冻结快照（如果 user_profile_enabled）
  ├─ build_system_prompt()               ← 外部 provider 的动态文本
  └─ 时间戳 + session_id + model
```

USER.md 只要 `user_profile_enabled` 就注入；MEMORY.md 需要 `memory_enabled`。

外部 provider 的 `system_prompt_block()` 跟在 builtin 后面。builtin 的文本来自冻结快照，外部 provider 的文本每次 rebuild 时动态调用。

---

## 第五层：StreamingContextScrubber

位于 `memory_manager.py`，是一个状态机。如果外部 provider 的 prefetch 输出里带了 `<memory-context>` 标签，它会在流式输出时逐 chunk 过滤掉。防止外部 provider 把内容包在 `<memory-context>` 标签里混过注入检查。

---

## 数据流

```
Session 启动
  → MemoryStore.load_from_disk()
  → 冻结快照: MEMORY.md + USER.md 内容进入 _system_prompt_snapshot
  → 构建系统提示词, 快照注入 volatile 层

每轮开始
  → prefetch_all(user_message)  ← 外部 provider 回忆/搜索
  → 结果追加到消息上下文 (不在系统提示词里)

每轮中
  → 模型调用 memory tool (add/replace/remove)
  → 立即写磁盘 (原子替换)
  → 系统提示词不变 (frozen)

每轮结束
  → sync_all(user, asst)  ← 外部 provider 异步持久化

下次 Session
  → 重新 load_from_disk()
  → 新快照包含上次 session 写入的所有内容
```

---

## 关键源文件

| 文件 | 职责 |
|---|---|
| `agent/memory_provider.py` | MemoryProvider 抽象基类，定义外部 provider 接口 |
| `agent/memory_manager.py` | MemoryManager 编排层，管理 builtin + 一个外部 provider |
| `tools/memory_tool.py` | MemoryStore 实现 + memory 工具 schema 注册 |
| `agent/system_prompt.py` | 系统提示词组装，记忆块注入到 volatile 层 |
| `agent/agent_init.py` | agent 初始化时创建 MemoryStore 并加载快照 |
| `tests/tools/test_memory_tool.py` | 相关测试 |
