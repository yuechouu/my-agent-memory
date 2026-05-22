# 记忆系统重新设计 — 多 Agent 协作提示词

## 任务

设计一个面向多 Agent 协作的下一代记忆系统。你（Kilo）负责技术架构设计，其他 Agent（noor、hanako 等）提供领域知识和需求约束。输出完整设计文档。

## 背景

### 当前系统（Hermes-memory v1）

```
热层(Hot)：   Markdown 文件注入到系统 prompt
温层(Warm)：  SQLite + FTS5 全文搜索，33 条记忆
冷层(Cold)：  会话存档 FTS5 搜索，237 个历史 session
做梦引擎：    Cron 定时 promote/archive，无降级、无清理、无评分
```

**已有功能：**
- 三层记忆分离（hot/warm/cold）
- FTS5 全文搜索 + 中文 LIKE fallback
- Cron 定时自动晋升和归档

**缺失功能：**
- 无语义/向量搜索（设计了但没写代码）
- 无降级（hot→warm）和生命周期清理
- 无跨 session 记忆整合/摘要
- 多 Agent 之间记忆不共享
- 评分只靠 access_count，无时间衰减
- 没区分个人记忆和共享记忆

### 两套不同的记忆系统

当前用户有两个 Agent，各自使用不同的记忆系统：

| | noor (Hermes) | hanako (Hermes 原生) |
|--|---------------|---------------------|
| 存储 | SQLite + FTS5 | MEMORY.md/USER.md 文本 |
| 搜索 | FTS5 BM25 + 中文 fallback | 无搜索，直接注入 |
| 生命周期 | Dreaming (promote/archive) | Frozen snapshot |
| 快照 | 无 | 启动时冻结，保持 prefix cache |
| 原子写入 | 无 | 临时文件 → fsync → rename |
| 注入扫描 | 无 | 检测隐形 Unicode、注入模式 |
| 外部 provider | 无 | 支持插件式外部后端 |
| 代码位置 | `E:\hermes\project\hermes-memory\` | `hermes-agent/agent/memory_manager.py` |

hanako 的完整架构文档：`E:\hana\ming\hanako-memory-architecture.md`

这两个系统需要统一成一个共享架构。

### 多 Agent 环境

用户同时运行多个 AI Agent：

| Agent | 平台 | 职责 |
|-------|------|------|
| noor | Hermes | 主力助手，拥有最多记忆 |
| hanako | — | 辅助 agent，需要共享记忆 |
| Kilo | Kilo Code | 编码专家，需要项目上下文 |
| OpenClaw agent | Gateway | 服务器端，通过 mesh-hub 通信 |
| Claude Code | Claude Code | 编码 agent，DeepSeek 后端 |

这些 agent 目前记忆隔离，需要一个共享记忆层。

### 设计原则

1. **能力优先**：动手前先问"这个协议/存储能支持这件事吗"——不假设，不先写代码再验证
2. **干净架构**：做新不要留旧，不保留向后兼容层
3. **本地优先**：纯 Python import > MCP 协议层，本地解决 > 云服务
4. **独立工具**：各工具独立命名空间，不寄生在其他模块下
5. **间隔调度**：定时任务用 interval（every 6h），不用壁钟（0 3 * * 0）

## 设计范围

### Phase 1：核心架构（本次）

**1. 记忆领域模型**
- 记忆条目的数据结构（字段、类型）
- 实体关系（agent ↔ 记忆、session ↔ 记忆、项目 ↔ 记忆）
- 命名空间和归属模型

**2. 存储后端**
- SQLite vs 向量数据库的取舍
- 混合搜索（FTS5 + 向量 + RRF 融合）
- 索引和查询策略

**3. 记忆生命周期**
- 完整 CRUD + 软删除
- 晋升/降级评分公式（访问频率 × 时间衰减 × 来源可信度）
- 自动整合（合并相关记忆）
- 清理策略

**4. 多 Agent 访问**
- 共享 vs 私有记忆
- 权限模型（每个 agent 的读写权限）
- 冲突解决（两个 agent 写下矛盾的事实）
- 记忆同步协议

**5. API 设计**
- Python SDK 接口
- CLI 命令
- MCP 集成（仅在有充分理由时）

### Phase 2：实现（后续）

- 详细数据模型（SQL DDL）
- Python 模块结构
- v1 → v2 迁移方案
- 测试策略

## 产出物

一份设计文档（markdown），包含：

1. **架构总览** — 高层架构图，展示所有组件和数据流
2. **领域模型** — 实体定义、关系、归属规则
3. **存储设计** — 表结构、索引策略、搜索管线
4. **生命周期状态机** — 所有状态和转换条件
5. **多 Agent 协议** — 共享方式、冲突解决、同步机制
6. **API 契约** — 方法签名和输入输出类型
7. **迁移方案** — 如何从 v1 迁移，不破坏现有记忆
8. **待讨论问题** — 还需要研究或讨论的开放问题

## 协作说明

- 可以向 noor 询问当前 v1 记忆系统的痛点
- 可以向 hanako 询问需要什么样的共享记忆
- 遇到有取舍的架构决策时，列出选项和利弊——用户喜欢主动选择而非接受默认
- 用户（pxlyu）可以随时澄清需求或偏好

## hanako 记忆架构（完整）

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


---

## 参考资料

- 当前记忆代码：`E:\hermes\project\hermes-memory\src\hermes_memory\`
- 当前记忆数据：`E:\hermes\hermes-data\memories\memory.db`
- 架构文档：`C:\Users\pxlyu\MEMORY_ARCHITECTURE.md`
- 用户画像：`E:\hermes\hermes-data\memories\USER.md`
