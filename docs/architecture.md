# My Agent Memory — 多 Agent 共享记忆系统架构设计

> 2026-05-22 · 经 butter/hanako/ming 审议 + noor 独立评估 · [审议详情](./review.md) · 已实现

---

## 1. 架构总览

```
                      ┌─────────────────────────────┐
                      │      Dreaming Engine         │
                      │  (每 6h cron / interval)      │
                      │  评分 → 迁移 → 冲突检测        │
                      └─────────────┬───────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │                         │                         │
          v                         v                         v
┌─────────────────┐     ┌────────────────────┐     ┌─────────────────┐
│    Hot 层        │     │     Warm 层         │     │    Cold 层       │
│  Markdown 投影   │     │   SQLite + FTS5    │     │   archived=1    │
│  frozen snapshot │◄───│   + sqlite-vec     │────▶│   保留 365 天   │
│  per-agent 目录  │     │   唯一数据源        │     │                   │
└────────┬────────┘     └─────────┬──────────┘     └─────────────────┘
         │                        │
         │   agent session 启动    │  agent 每轮对话
         │   注入 system prompt    │  search/hybrid_search/save
         v                        v
   ┌──────────┐          ┌──────────────┐
   │  hanako  │          │  MultiAgent  │
   │  消费端  │          │    Store     │
   │ 截取预算 │          │  Python SDK  │
   └──────────┘          └──────┬───────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         v                      v                      v
   ┌──────────┐          ┌──────────┐          ┌──────────┐
   │   noor   │          │   kilo   │          │ openclaw │
   │ 消费端   │          │ 消费端   │          │  消费端  │
   └──────────┘          └──────────┘          └──────────┘
```

**核心理念**：SQLite 是唯一数据源。Hot 层 Markdown 文件是 SQLite 的确定性投影（deterministic projection），不是独立存储。v2 不控制消费端截断——每个 agent 的 MemoryManager 按自己的 token 预算截取 hot 层输出。

**安全边界**：所有记忆写入前经过 `validate.py` 同步 gate（长度/特殊字符/已知注入模式检查），异步 LLM 语义检查作为第二道防线。被同步 gate 拦截的内容直接拒绝写入。

---

## 2. 领域模型

### 2.1 记忆实体 (memory_entries)

```python
@dataclass
class MemoryEntry:
    id: int                          # 自增主键
    content: str                     # 记忆正文
    title: str                       # 简短标题
    tags: list[str]                  # 标签
    source: str                      # manual / agent / imported / consolidated
    checksum: str                    # MD5 前 12 位，同 owner 内去重（跨 owner 不去重，见 5.3）
    
    # 归属与可见性
    owner_agent: str                 # "noor" / "hanako" / "kilo" / "openclaw" / "claude"
    scope: str                       # private / shared / project
    project: str | None              # scope=project 时所属项目名
    
    # 生命周期状态
    state: str                       # raw / promoted / hot / archived / deleted
    is_pinned: bool                  # pin 锚定，True 时 dreaming 跳过降级/归档（评分仍追踪）
    promoted_at: str | None
    archived_at: str | None
    deleted_at: str | None
    
    # 评分（仅 access_count + last_access_ts，完整审计走 dreaming_log）
    access_count: int                # 搜索命中 + get 调用次数
    last_access_ts: str              # 最近一次被访问的时间戳（ISO 8601）
    score: float                     # 当前评分，dreaming 更新
    
    # 向量
    embedding: bytes | None          # 2048 维 float32 blob
    embedding_model: str | None      # "Qwen3-Embedding-8B"
    
    # 关联
    consolidated_from: list[int]     # JSON: 哪些条目被合并成这条
    superseded_by: int | None        # 被哪条代替
    
    # 时间戳
    created_at: str
    updated_at: str
```

### 2.2 命名空间与可见性

```
owner_agent: 谁写的这条记忆
scope:       private — 只有 owner_agent 可见
             shared  — 所有 agent 可见
             project — 同 project 的 agent 可见
project:     scope=project 时有效
```

**可见性规则（SQL 查询级强制执行）**：

| requester | 可见范围 |
|-----------|---------|
| noor | owner=noor AND scope=private; OR scope=shared; OR scope=project AND project 匹配 |
| hanako | owner=hanako AND scope=private; OR scope=shared; OR scope=project AND project 匹配 |
| ... | （类推） |

**共享记忆产生途径**（非 dreaming 自动推动）：
1. Agent 主动标记：`store.save(content, scope="shared")`
2. 用户 CLI 手动提升：`hermes-memory share <id>`

### 2.3 实体关系

```
Agent ──owns──▶ MemoryEntry (N:1, 一条记忆只有一个 owner)
Agent ──access──▶ MemoryEntry (N:M, 通过 scope 规则)
Project ──contains──▶ MemoryEntry (N:M, scope=project)
DreamingLog ──references──▶ MemoryEntry (N:M, 记录哪次 dreaming 操作了哪些条目)
Conflict ──involves──▶ MemoryEntry (2:2, 冲突中的两条记忆)
-- 未来预留: Agent ──confirms──▶ MemoryEntry (N:M, cross-agent confirmation)
```

---

## 3. 存储设计

### 3.1 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 主存储 | SQLite WAL 模式 | 本地优先，零服务依赖，并发读安全 |
| 全文搜索 | FTS5 (unicode61 tokenizer) | v1 已有，成熟稳定 |
| 向量搜索 | sqlite-vec v0.1.9 | 原生扩展，win_amd64 已验证 |
| 向量模型 | Qwen/Qwen3-Embedding-8B | SiliconFlow，2048 维，批量 API |
| embedding 策略 | 写入异步生成，checksum 缓存 | 不阻塞写入，同内容不重复嵌入 |

**拒绝的方案**：
- ChromaDB/Qdrant：需要独立进程，Windows 兼容差，本地优先原则否决
- 纯文本文件：搜索不可扩展，33 条还行，300 条崩溃

### 3.2 SQLite 表结构

```sql
-- 核心记忆表
CREATE TABLE memory_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    title           TEXT DEFAULT '',
    tags            TEXT DEFAULT '[]',
    source          TEXT DEFAULT 'manual',
    checksum        TEXT,
    
    -- 归属
    owner_agent     TEXT NOT NULL DEFAULT 'noor',
    scope           TEXT NOT NULL DEFAULT 'private',  -- private / shared / project
    project         TEXT,
    
    -- 生命周期
    state           TEXT NOT NULL DEFAULT 'raw',  -- raw / promoted / hot / archived / deleted
    is_pinned       INTEGER DEFAULT 0,            -- pin 锚定，dreaming 跳过降级/归档
    promoted_at     TEXT,
    archived_at     TEXT,
    deleted_at      TEXT,
    
    -- 评分
    access_count    INTEGER DEFAULT 0,
    last_access_ts  TEXT,                         -- 最近访问时间，评分衰减用
    score           REAL DEFAULT 0.0,
    
    -- 向量
    embedding       BLOB,             -- 2048 × 4 bytes = 8192 bytes
    embedding_model TEXT,
    
    -- 关联
    consolidated_from TEXT DEFAULT '[]',
    superseded_by   INTEGER,
    
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- FTS5 全文索引
CREATE VIRTUAL TABLE memory_fts USING fts5(
    title,
    content,
    tags,
    tokenize='unicode61',
    content='memory_entries',
    content_rowid='id'
);

-- 向量索引（sqlite-vec）
-- 通过 sqlite_vec.load() 加载，在 Python 中创建
-- CREATE VIRTUAL TABLE memory_vec USING vec0(
--     embedding float[2048]
-- );

-- FTS 同步触发器
CREATE TRIGGER mem_ai AFTER INSERT ON memory_entries BEGIN
    INSERT INTO memory_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;

CREATE TRIGGER mem_ad AFTER DELETE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
END;

CREATE TRIGGER mem_au AFTER UPDATE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
    INSERT INTO memory_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;

-- 冲突表
CREATE TABLE memory_conflicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_a_id      INTEGER NOT NULL,
    entry_b_id      INTEGER NOT NULL,
    similarity      REAL,               -- cosine similarity
    reason          TEXT,               -- 为什么判定为冲突
    status          TEXT DEFAULT 'open', -- open / resolved_a / resolved_b / merged / dismissed
    resolved_by     TEXT,               -- agent 或 user
    resolved_at     TEXT,
    merged_into     INTEGER,            -- resolve=merged 时指向新条目
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Dreaming 日志
CREATE TABLE dreaming_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT DEFAULT (datetime('now')),
    candidates      INTEGER,
    promoted        INTEGER,
    demoted         INTEGER,
    archived        INTEGER,
    purged          INTEGER,
    conflicts_found INTEGER,
    details         TEXT
);

-- 索引
CREATE INDEX idx_entries_owner ON memory_entries(owner_agent);
CREATE INDEX idx_entries_scope ON memory_entries(scope);
CREATE INDEX idx_entries_state ON memory_entries(state);
CREATE INDEX idx_entries_project ON memory_entries(project);
CREATE INDEX idx_entries_score ON memory_entries(score DESC);
CREATE INDEX idx_entries_access ON memory_entries(access_count DESC);
CREATE INDEX idx_entries_checksum ON memory_entries(checksum);
```

### 3.3 搜索管线

```
用户 query
    │
    ├─ 条目 >= 100 条:
    │   query → FTS5 BM25 粗筛 (top 50) → 向量余弦相似度 (top 50 → top 20) → RRF 融合 → top 10
    │
    └─ 条目 < 100 条:
        query → FTS5 BM25 (全量) + 向量余弦相似度 (全量) → RRF 融合 → top 10
```

**RRF 融合公式**（Reciprocal Rank Fusion）：

```
RRF(entry) = Σ 1 / (k + rank_i)   其中 k = 60
```

FTS5 排名 + 向量排名各自算分，RRF 融合后统一排序。这是标准的无参数混合搜索方法。

**中文搜索**：FTS5 unicode61 对中文逐字分词导致多字符词搜索不可靠。v1 的 CJK LIKE fallback 保留。向量搜索对中文天然友好（语义匹配），作为主路径后 CKJ fallback 可逐步退场。

**embedding 空窗期处理**：写入时向量异步生成，新记忆在 embedding 完成前的搜索中，向量路径跳过无 embedding 的条目（不参与向量排名），仅靠 FTS5 路径兜底。embedding 生成超时未完成时告警。

### 3.4 Hot 层 Markdown 投影

```
{hermes_home}/memories/
├── shared/
│   └── MEMORY.md           ← scope=shared 的记忆
├── noor/
│   ├── MEMORY.md           ← noor 的 promoted/hot 记忆（按 score 降序）
│   └── USER.md             ← 用户画像（不变）
├── hanako/
│   ├── MEMORY.md
│   └── USER.md
├── kilo/
│   └── MEMORY.md
├── openclaw/
│   └── MEMORY.md
└── memory_v2.db            ← SQLite 唯一数据源
```

**关键设计**：
- v2 产出完整 hot 层投影，**不做截断**。每个 agent 的 MemoryManager 按自己的 token 预算截取
- hanako 继续用 2200 字符上限，在 `system_prompt.py` 里截取——这件事不在 v2 里
- 条目按 score 降序排列，agent 按优先级取前 N 条，截掉的是低分内容而非随机截断
- 写入走 `temp → fsync → rename`（hanako 的原子写），兼容 frozen snapshot 模式

---

## 4. 生命周期状态机

### 4.1 状态转换图

```
                    ┌──────────────┐
                    │   deleted    │
                    │  (硬删除)     │
                    └──────────────┘
                          ▲
                          │ purge (archived 超 365 天)
          ┌───────────────┴───────────────┐
          │      (任何状态都可 archive)    │
          │                               │
   ┌──────┴──────┐                  ┌─────┴──────┐
   │    raw      │─────────────────▶│  archived  │
   │  (Warm 层)  │   archive        │ (Cold 层)  │
   └──────┬──────┘                  └────────────┘
          │ promote                          ▲
          │ (score >= 3.0)                   │
          │                                  │ archive
   ┌──────┴──────┐                  ┌───────┴──────┐
   │  promoted   │─────────────────▶│  archived    │
   │ (Hot 候选)  │   archive        │              │
   └──────┬──────┘                  └──────────────┘
          │ consolidate
          │ (LLM 合并 promoted 中的相关条目)
   ┌──────┴──────┐
   │    hot      │──▶ demote  ──▶ raw       (score < 1.0)
   │ (已写 .md)  │──▶ demote  ──▶ archived  (score < 0.1)
   └─────────────┘
```

### 4.2 转换条件与阈值

| 转换 | 条件 | 执行者 | 频率 |
|------|------|--------|------|
| raw → promoted | score >= 3.0 | dreaming | 每 6h |
| promoted → hot | consolidate 完成 | dreaming | 每 6h |
| hot → raw | score < 1.0（热度下降） | dreaming | 每 6h |
| any → archived | score < 0.1 或显式 archive | dreaming / agent / user | 每 6h |
| archived → deleted | archived_at > 365 天 | dreaming | 每 6h |
| **任何降级/归档** | **is_pinned=true 时跳过上述所有自动降级/归档** | — | — |

### 4.3 评分公式

```python
score = log2(access_count + 1) x e^(-lambda x days_since_last_access) x source_weight

其中:
  lambda = ln(2) / half_life_days        # half_life = 30 天（默认）
  days_since_last_access = (now - last_access_ts).days  # 来自 last_access_ts 字段
  
  source_weight:
    manual      = 1.0               # 用户手写，最高信任
    agent       = 0.8               # agent 生成
    imported    = 0.5               # 批量导入
    consolidated = 0.9              # LLM 合并生成

参数可配置:
  half_life_days     = 30           # 时间衰减半衰期
  promote_threshold  = 3.0          # 晋升门槛
  demote_threshold   = 1.0          # 降级门槛
  archive_threshold  = 0.1          # 归档门槛
  purge_days         = 365          # 硬删除天数
```

**与 v1 的差异**：v1 只用 `access_count >= 2`，不区分时间和来源。v2 的评分同时考虑频率、时间和可信度。完整访问审计走 `dreaming_log` 表，不存储于记忆实体中。

---

## 5. 多 Agent 协议

### 5.1 默认隔离，显式共享

- 所有记忆写入默认 `scope=private`，仅 owner 可见
- 共享记忆（`scope=shared` 或 `scope=project`）由 agent 主动标记或用户 CLI 提升
- 不会出现"所有 agent 的记忆混在一起"的情况

### 5.2 权限模型

| 操作 | 自己 private | 自己 shared | 别人 private | 别人 shared | project |
|------|------------|------------|-------------|------------|---------|
| 读 | ✓ | ✓ | ✗ | ✓ | ✓ |
| 写/更新 | ✓ | ✓ | ✗ | ✗ | ✗（仅 owner） |
| pin/unpin | ✓ | ✓ | ✗ | ✗ | ✗ |
| archive | ✓ | ✓ | ✗ | ✗ | ✗ |
| delete | ✗（仅 dreaming purge） |

**原则**：只能写自己的，shared 只共享只读访问。写他人记忆的唯一途径是 conflict resolve 的 merge。

### 5.3 冲突检测

| 维度 | 设计 |
|------|------|
| 触发时机 | dreaming 每 6h 扫描 |
| 检测范围 | shared 和 project 范围内的记忆 |
| 检测方法 | cosine_similarity(emb_a, emb_b) > 0.9 且内容推断为矛盾 |
| 矛盾推断 | 用 LLM（DeepSeek Flash）判断两条记忆是否在陈述矛盾的事实 |
| 存储 | `memory_conflicts` 表 |
| 通知 | `hermes-memory conflicts` CLI 或 agent 查询 |

**不做实时冲突解决**——多 agent 同时在线写矛盾事实的概率极低，事后批量处理更高效。

### 5.4 冲突解决策略

| 策略 | 说明 |
|------|------|
| `last_write_wins` | 保留最新的，旧条目 `superseded_by` 指向新条目 |
| `keep_both` | 两条都保留，用户审核 |
| `merge` | LLM（DeepSeek Flash）合并为一条新记忆 |

### 5.5 同步协议

不引入实时同步。v2 是**单写入者**架构：

- 只有 dreaming engine 写入 hot 层（定时批量）
- agent 只写 SQLite（自己的条目）
- hot 层更新在下次 session 启动时通过 frozen snapshot 自然生效

这避免了分布式锁/vector clock/CRDT 等复杂协议的必要性。

### 5.6 去重策略

**同 owner 内去重，跨 owner 不去重。**

- `store.save()` 写入时，checksum 匹配 + 同 owner_agent → 视为重复写入，更新该条目的 access_count 和 updated_at，不新增行
- checksum 匹配 + 不同 owner_agent → 保留两条独立记录。不同 agent 独立得出相同结论不是冗余，是多方独立验证信号

**未来预留**：跨 owner checksum 匹配时，可在未来版本引入 `confirmed_by` 关系——"这条记忆被 N 个 agent 独立验证过"，为信任评分铺路。v2 不加此机制，仅在设计注释中留口子。

---

## 6. API 契约

### 6.1 Python SDK（MultiAgentStore）

```python
from hermes_memory_v2 import MultiAgentStore

store = MultiAgentStore(
    db_path="path/to/memory_v2.db",
    agent_id="noor",
    hermes_home="E:/hermes/hermes-data",
    config={
        "embedding": {
            "model": "Qwen/Qwen3-Embedding-8B",
            "base_url": "https://api.siliconflow.cn/v1",
            "batch_size": 10,
        },
        "scoring": {
            "half_life_days": 30,
            "promote_threshold": 3.0,
            "demote_threshold": 1.0,
            "archive_threshold": 0.1,
            "purge_days": 365,
        },
        "consolidate_model": "deepseek/deepseek-flash",
    }
)

# ── CRUD ──

entry: dict = store.save(
    content: str,
    title: str = "",
    tags: list[str] = None,
    source: str = "manual",
    scope: str = "private",
    project: str = None,
)  # 返回完整 entry dict，写入 SQLite，异步生成 embedding

entry | None = store.get(entry_id: int)
entry | None = store.update(entry_id: int, **fields)
entry | None = store.archive(entry_id: int)
bool = store.delete(entry_id: int)  # 仅 archived > 365d

# ── 搜索 ──

list[dict] = store.search(
    query: str,
    limit: int = 10,
    offset: int = 0,
    tags: list[str] = None,
    scope: str = None,        # None = 自己的 private + shared + project
    agent_id: str = None,     # 跨 agent 搜索时指定
)

list[dict] = store.hybrid_search(
    query: str,
    limit: int = 10,
    scope: str = None,
    agent_id: str = None,
    fts_weight: float = 0.5,   # FTS5 在 RRF 中的权重
    vec_weight: float = 0.5,
)

# ── 共享操作 ──

entry | None = store.share(entry_id: int)  # private → shared
entry | None = store.unshare(entry_id: int)  # shared → private（仅 owner）

# ── Pin 锚定 ──

entry | None = store.pin(entry_id: int)    # is_pinned = True，dreaming 不再自动降级
entry | None = store.unpin(entry_id: int)  # 恢复自动生命周期管理

# ── 生命周期 ──

dict = store.dreaming(dry_run: bool = True) -> {
    "dry_run": bool,
    "candidates": list[dict],
    "promoted": list[int],
    "demoted": list[int],
    "archived": list[int],
    "purged": list[int],
    "consolidated": list[dict],
    "conflicts_found": int,
}

entry | None = store.consolidate(entry_ids: list[int])
# → LLM 合并相关记忆为一条，原条目标记 superseded_by

# ── 冲突 ──

list[dict] = store.get_conflicts(status: str = "open")
dict | None = store.resolve_conflict(
    conflict_id: int,
    strategy: str,        # last_write_wins / keep_both / merge
    merged_content: str = None,  # merge 时需要
)

# ── 统计与维护 ──

dict = store.stats() -> {
    "total": int, "by_state": dict, "by_scope": dict,
    "by_agent": dict, "db_path": str, "last_dreaming": str,
}
store.rebuild_indexes()
store.rebuild_hot_layer()

# ── 快照 ──

str = store.get_system_prompt_block(agent_id: str, max_chars: int = None)
# → 返回 hot 层内容（按 score 排序，可选截断），给 agent 注入系统 prompt

# ── 迁移 ──

dict = store.migrate_from_v1(
    v1_db_path: str,
    v1_hot_dir: str,
    agent_id: str,
    dry_run: bool = True,
)
```

### 6.2 CLI

```bash
# 兼容 v1 命令
hermes-memory search <query> [--limit N] [--tags t1,t2] [--scope shared|private]
hermes-memory save <content> [--title "..."] [--tags t1,t2] [--scope shared]
hermes-memory get <id>
hermes-memory status
hermes-memory dream [--execute] [--dry-run]
hermes-memory rebuild
hermes-memory archive <id>

# v2 新增命令
hermes-memory hybrid <query> [--limit N] [--scope shared]
hermes-memory share <id>
hermes-memory unshare <id>
hermes-memory pin <id>
hermes-memory unpin <id>
hermes-memory conflicts [--resolve <id> --strategy merge|keep_both|last_write_wins]
hermes-memory consolidate [--ids 1,2,3]

# 迁移
hermes-memory migrate --v1-db <path> --v1-hot <dir> --agent <id> [--execute]
```

---

## 7. 迁移方案

### 7.1 策略

**增量迁移，不破坏 v1 数据**。

```
v1                                      v2
──                                      ──
hermes-memory/src/hermes_memory/        hermes-memory-v2/src/hermes_memory_v2/
                                        （新代码，独立项目）

memory.db                               memory_v2.db
  memory_entries ─── 迁移 ───▶            memory_entries (加列: owner_agent, scope,
  memory_fts    ─── 重建 ───▶                          project, state, score, embedding)
  dreaming_log  ─── 保留 ───▶                          memory_fts (重建)
                                                       memory_vectors (新建)
                                                       dreaming_log (保留)

memories/*.md                           memories/noor/*.md  (按 agent 分目录)
  MEMORY.md    ─── 移到 ───▶              MEMORY.md + USER.md
  USER.md                               memories/shared/MEMORY.md (新建)
  *.md (topic)
```

### 7.2 迁移步骤

```bash
# 1. 预览（不改任何文件）
hermes-memory migrate \
  --v1-db E:/hermes/hermes-data/memories/memory.db \
  --v1-hot E:/hermes/hermes-data/memories \
  --agent noor \
  --dry-run

# 输出:
#   - 33 条 warm 层记忆 → 加 owner_agent="noor", scope="private"
#   - 6 个 topic .md 文件 → 移到 memories/noor/
#   - MEMORY.md → 保留
#   - USER.md → 移到 memories/noor/
#   - 0 条已有 shared 记忆

# 2. 确认后执行
hermes-memory migrate \
  --v1-db E:/hermes/hermes-data/memories/memory.db \
  --v1-hot E:/hermes/hermes-data/memories \
  --agent noor \
  --execute

# 3. 迁移后:
#   - memory.db 重命名为 memory_v1_backup.db
#   - 新建 memory_v2.db
#   - hot 文件移动到 per-agent 目录
```

### 7.3 hanako 对接：双层架构

hanako 不将全部经验写入 Hermes SQLite，保持双层共存：

```
hanako 本地经验库（高频、细粒度、即时生效）
    │
    │ 手动提升 / dreaming consolidate（低频、慎重）
    ▼
Hermes v2 共享记忆（结构化、跨 agent、评分驱动）
```

- hanako 保持自己的快速学习循环（`record_experience` / `recall_experience`）
- 需要共享的经验通过 `store.save(scope="shared")` 主动提升
- dreaming 时 consolidate 也可将 hanako 相关条目合并后提升
- v2 API 层预留写入路径，但不强制全量同步

**hanako 侧改动**：
- Hot 层路径：`memories/hanako/MEMORY.md`（新增自己的命名空间目录）
- 可选对接 `MultiAgentStore.hybrid_search()` 替代纯文本搜索
- 不改动本地经验库的写入逻辑

---

## 8. 模块结构

```
hermes-memory-v2/
├── src/hermes_memory_v2/
│   ├── __init__.py          # 导出 MultiAgentStore, StoreConfig
│   ├── store.py             # 顶层 API，agent 入口
│   ├── db.py                # SQLite schema 初始化 + 基础 CRUD
│   ├── search.py            # FTS5 + 向量 + RRF 搜索管线
│   ├── embed.py             # SiliconFlow embedding 封装（批量、缓存、异步）
│   ├── dreaming.py          # 生命周期引擎（评分 → 状态转换 → 写 hot 层）
│   ├── scoring.py           # 评分公式（纯函数，access_count + last_access_ts）
│   ├── conflicts.py         # 冲突检测 + 解决
│   ├── validate.py          # 注入扫描（写入时同步阻断 + 异步 LLM 补检）
│   ├── hot_layer.py         # Markdown 投影生成（原子写）
│   ├── provider.py          # MemoryProvider ABC（外部后端接口，预留）
│   ├── migrate.py           # v1 → v2 迁移
│   └── cli.py               # CLI（兼容 v1 命令 + v2 新命令）
├── tests/
│   ├── test_db.py
│   ├── test_search.py
│   ├── test_scoring.py
│   ├── test_dreaming.py
│   ├── test_conflicts.py
│   ├── test_hot_layer.py
│   └── test_migrate.py
├── pyproject.toml
└── README.md
```

---

## 9. 待讨论问题

| # | 问题 | 状态 | 结论 |
|---|------|------|------|
| 1 | consolidate 用 LLM 还是规则匹配？ | ✅ 已确认 | DeepSeek Flash，便宜快速 |
| 2 | hanako 外部 provider 接口是否在 v2 保留？ | ✅ 已确认 | 保留 ABC 但不实现；hanako 走双层架构 |
| 3 | 向量维度 2048 在 sqlite-vec 上的实际性能？ | 待压测 | 33 条无压力，300+ 条时验证 |
| 4 | 冲突检测的"语义矛盾"判断 prompt？ | 待实现 | DeepSeek Flash 二分类：矛盾/不矛盾 |
| 5 | 每次 dreaming 重算所有 entry 的 score？ | ✅ 已确认 | access_count + last_access_ts 字段级计算，33-300 条毫秒级 |
| 6 | hot 层原子写是 v2 做还是交给 agent？ | ✅ 已确认 | v2 做（hot_layer.py temp+fsync+rename） |
| 7 | pin 机制 | ✅ 已确认 | `is_pinned` 布尔字段，纳入 v2（butter/ming/noor 一致同意） |
| 8 | access_history 砍掉 | ✅ 已确认 | 砍掉，用 access_count + last_access_ts（hanako/noor 一致同意） |
| 9 | checksum 去重粒度 | ✅ 已确认 | 同 owner 内去重，跨 owner 保留（见 5.6） |
| 10 | validate.py 触发时机 | ✅ 已确认 | 写入时同步阻断（简单规则）+ 异步 LLM 补检 |
| 11 | hanako 经验库与 Hermes 对接 | ✅ 已确认 | 双层架构，不强制全量写入（见 7.3） |
| 12 | cross-agent confirmation 关系 | 留口子 | v2 不加表，设计注释留 `confirmed_by` 未来入口（noor 建议） |

### 设计口子（v3 或后续）

- **cross-agent confirmation**：跨 owner checksum 匹配时建立 `confirmed_by` 关系，用于信任评分（"这条记忆被 N 个 agent 独立验证过"）
- **embedding 空窗期**：新记忆写入后向量异步生成期间的搜索策略——当前 FTS5 兜底，无向量条目在向量路径跳过而非打 0 分
- **dreaming 6h vs agent 巡检 ~30min** 节奏差：影响有限，agent 对话中即时写入的纠正记忆在当前 session 已生效，hot 层延迟到下次 dreaming 更新不影响当前行为

---

## 10. 参考资料

- Hermes v1 代码：`E:\hermes\project\hermes-memory\src\hermes_memory\`
- Hermes v1 数据：`E:\hermes\hermes-data\memories\memory.db`
- Hermes v1 架构：`C:\Users\pxlyu\MEMORY_ARCHITECTURE.md`
- Hanako 架构：`E:\hana\ming\hanako-memory-architecture.md`
- 设计任务书：`C:\Users\pxlyu\MEMORY_REDESIGN_PROMPT.md`
