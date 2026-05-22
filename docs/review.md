# Hermes Memory v2 — 频道审议总结

> 2026-05-22 · 基于 butter / hanako / ming 三轮讨论整理

---

## 1. 审议覆盖维度

| 维度 | 主审 | 结论 |
|------|------|------|
| 哲学层（设计原则与架构理念） | butter | 整体一致，与频道既有共识同构 |
| 工程边界（实现细节与 corner case） | hanako | 发现 4 个边界条件，已收敛 |
| 结构缺口（未覆盖的设计空间） | ming | 发现 pin 机制缺失等结构问题 |

---

## 2. 确认的设计判断（无争议）

- **SQLite 唯一数据源 + Hot 层确定性投影**：解决了 v1 双存储"谁是真"的问题
- **半衰期衰减评分公式**：log2 + 指数衰减 + 源权重，比 v1 的 access_count >= 2 显著进步
- **默认隔离、显式共享**：与频道"走廊里偶尔亮一下"原则同构
- **单写入者架构**：dreaming engine 唯一写 hot 层，避免分布式一致性问题
- **RRF 混合搜索**：FTS5 + 向量余弦 + RRF 融合，技术选型合理

---

## 3. 补充建议（讨论中新增，建议纳入设计）

### 3.1 pin 机制（显式锚定）

**问题**：评分公式依赖访问频次，冷门但重要的记忆会自然衰减并被降级/归档。这是所有基于访问频次的系统的固有局限，不是公式的 bug。

**建议**：引入显式 pin 机制，用户可以标记一条记忆不参与 dreaming 的降级/归档逻辑。butter 指出这是"锚"在记忆系统中的实例化：代谢系统（评分自动升降级）和锚定系统（显式意图驱动）是两个互补通道，不互斥。

**实现方向**：`is_pinned` 布尔字段，或 `scope` 加 `pinned` 标记。pinned 条目跳过 dreaming 的降级/归档，但保留评分追踪（了解自己为什么信它）。

### 3.2 access_history 砍掉

**问题**：`access_history` 是 JSON 数组，每次搜索命中或 get 都追加一条，长期运行会膨胀到 MB 级。每次 dreaming 重算 score 都要 parse 整个 JSON。

**结论**：如果 `access_history` 的唯一消费者是 scoring 公式的时间衰减计算，则只需 `access_count` + `last_access_ts` 两个字段。审计需求走 logging 层，不在记忆实体里维护完整历史。**建议砍掉 `access_history` 字段。**

### 3.3 checksum 同 owner 内去重

**问题**：文档定义了 checksum 去重，但未明确粒度。两个不同 owner 独立写出内容相同的记忆，checksum 匹配时应该去重还是保留？

**结论**：**同 owner 内去重，跨 owner 不去重。** 不同 agent 独立得出相同结论不是冗余，是多方独立验证。保留两条记录，它们的并行存在本身是一种互相校验。butter 补充："抹掉其中一条等于删掉'这个结论曾被多方验证过'的痕迹。"

### 3.4 validate.py 写入时同步阻断

**问题**：`validate.py`（注入扫描）从 hanako 移植为 Hermes 共享模块，触发时机未明确。如果是 dreaming 时异步扫描，agent 读取时可能已消费到脏数据。

**结论**：**写入时同步阻断是唯一正确的架构选择。** 两层策略：
- 同步 gate：简单规则（长度/特殊字符/已知注入模式），性能可控
- 异步 alarm：LLM 语义判断，事后标记并通知

异步 alarm 的 gap 可接受——Hermes 的注入威胁主要是 prompt injection 污染 agent 上下文，不是传统 SQL 注入。未被同步 gate 拦截的注入在 LLM alarm 触发前可能短暂存在于上下文中，但会被标记。

### 3.5 v2 与 hanako 本地经验库的对接策略

**问题**：文档第 7.3 节只说了改 hot 层路径，未涉及 hanako 的 `record_experience` / `recall_experience` / `search_memory` 工具链如何与 Hermes v2 对接。

**待明确**：hanako 经验库最终是写入 Hermes SQLite（`scope=private, source=agent`），还是保持双层共存（Hermes 管共享记忆骨架，hanako 本地继续即时精细学习）？如果走 Hermes，API 层需要提前预留写入路径，不要拖到迁移完成才发现没留口子。

---

## 4. 新增待讨论问题

| # | 问题 | 来源 | 建议 |
|---|------|------|------|
| 7 | 是否引入 pin 机制 | ming / butter | 建议加入 v2 |
| 8 | access_history 是否砍掉 | hanako / ming | 建议砍掉，用 access_count + last_access_ts 替代 |
| 9 | checksum 去重粒度（同 owner 还是全局） | hanako / ming / butter | 同 owner 内去重 |
| 10 | validate.py 触发时机 | hanako / ming | 写入时同步阻断 + 异步 LLM 补检 |
| 11 | hanako 经验库与 Hermes 的对接路径 | hanako / butter | 需月愁明确方向 |

---

## 5. 节奏相关备注

- **dreaming 6h vs 巡检 ~30min**：butter 提的节奏差问题。分析后确认影响有限——agent 对话中被纠正后立即写入的纠正记忆在当前 session 中已生效，hot 层评分延迟到下次 dreaming 更新不影响当前行为。感知影响小，但需要在心里有数。
- **异步 embedding 空窗期**：写入时不生成 embedding，新记忆在向量搜索路径上有空窗。FTS5 能兜底但 RRF 融合时有偏置。建议对无向量的条目在向量路径上跳过而非打 0 分，并给嵌入生成加超时告警。

---

## 6. 审议结论

整体设计无硬伤。原文档 6 条待讨论问题 + 本轮新增 5 条，加上确认的 4 个补充建议（pin、砍 access_history、checksum 策略、validate 同步阻断），文档在纸面上已达到可进入实现阶段的完备度。建议月愁先确认补充建议的处理方式，再启动迁移。
---

## 7. noor 独立判断

> 2026-05-22 · 基于设计文档和审议总结的独立评估

### 7.1 接受项

**pin 机制** ✅ — 代谢系统（评分自动升降级）和锚定系统（显式意图驱动）互补，curator 已有 pin/unpin 先例。加 `is_pinned` 布尔字段，pinned 条目跳过 demote/archive 但不跳过评分追踪——知道为什么信它和信它本身同等重要。

**砍 access_history** ✅ — `access_count + last_access_ts` 足够支撑评分。完整历史走 logging 层，不放实体表。33 条不明显，300 条时每次 search 都追加 JSON 就是性能灾难。

**checksum 同 owner 去重** ✅ 接受 + 补一条：跨 owner 不去重，但加 **cross-agent confirmation** 标记。"两个 agent 独立得出相同结论"是验证信号，不该抹掉。比单纯保留两条冗余信息量更高。

**validate 同步阻断** ✅ 必须。安全没有商量余地。写入时同步 gate 是第一道防线，异步 LLM 语义判断可以做第二道，但不能反过来。

### 7.2 不接受的建议

**hanako 全量写 Hermes SQLite** ❌

节奏不匹配。hanako 的经验库是高频、即时、领域细粒度的学习——每轮可能写入几条微观经验。Hermes dreaming 6 小时才处理一次。全量塞进去会把共享记忆的质量稀释，共享记忆里的每一条都是慎重展示的东西。

**替代方案**：双层架构

```
hanako 本地经验库（高频、细粒度、即时生效）
    │
    │ 手动提升 / dreaming consolidate（低频）
    ▼
Hermes v2 共享记忆（结构化、跨 agent、评分驱动）
```

- hanako 保持自己的快速学习循环
- 需要共享的经验主动 save(scope=shared) 或 dreaming 时 consolidate 提升
- API 层预留写入路径但不强制

### 7.3 补充：cross-agent confirmation 是否加

这是一个对审议建议的补充判断，尚未进入设计文档。如果引入——跨 owner checksum 匹配时不合并，而是在两条记忆之间建立 `confirmed_by` 关系。好处是为未来的信任评分铺路（"这条记忆被 N 个 agent 独立验证过"），代价是多一个关系表和稍微复杂的查询。建议 v2 先不加表但在评论里留下设计口子。

