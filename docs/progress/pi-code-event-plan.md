# Fix Plan: Pi Code 记忆自动化三层机制

## Context

当前 Pi Code 扩展只有 2 个事件处理（`before_agent_start` 注入热层、`turn_end` 空实现）+ 工具 description 只说"做什么"不说"什么时候用"。需要三层机制互补。

## 三层机制总览

```
┌─────────────────────────────────────────────────┐
│ Layer 1: LLM Agent 主导 (Tool Description)       │
│ 每个 tool description 写清触发条件               │
│ Agent 根据上下文自己判断要不要调用               │
├─────────────────────────────────────────────────┤
│ Layer 2: System Prompt 指南                      │
│ before_agent_start 注入全局记忆使用规则           │
├─────────────────────────────────────────────────┤
│ Layer 3: 事件兜底 (Keyword Fallback)             │
│ Agent 没搜 + 用户消息含记忆词汇 → 兜底搜一次     │
│ input → 显式保存意图拦截                         │
│ turn_end → 自动提取                              │
└─────────────────────────────────────────────────┘
```

**核心原则：Agent 是主角，事件是安全网。**

## Layer 1: Tool Description 触发条件

更新每个 tool 的 description，加入"什么时候该用"：

| Tool | 改进后 Description |
|------|-------------------|
| memory_search | "Search memory. **Use when**: user asks about past interactions, preferences, project details, or before answering questions that might be in memory." |
| memory_save | "Save to memory. **Use when**: user shares important preferences, decisions, instructions, or explicitly says 'remember this'. Do NOT save temporary context." |
| memory_recall | "Structured recall with filters. **Use when**: you need precise filtering by type, scope, or tags. Prefer memory_search for simple queries." |
| memory_update | "Update memory. **Use when**: user corrects previously stored information or you notice contradictions." |
| memory_archive | "Archive memory. **Use when**: information is no longer relevant or user asks to forget." |
| memory_pin | "Pin memory. **Use when**: user emphasizes something is critical and should never be auto-archived." |

## Layer 2: System Prompt 指南

`before_agent_start` 注入：

```markdown
## Memory System
You have access to a persistent memory system. Use it actively.

### When to Search
- User asks about past interactions, preferences, or project details
- Before answering questions that might be in memory

### When to Save
- User shares important preferences, decisions, instructions
- User explicitly says to remember something
- You learn durable facts (not temporary context)

### When to Update
- User corrects previously stored information
- You notice contradictions between conversation and memory

### Memory Types
- procedural: workflows, how-to steps, instructions
- entity: facts about specific things (servers, tools, projects)
- knowledge: general concepts, theories, configurations
```

## Layer 3: 事件兜底

### `before_agent_start` — 热层 + 指南注入
```typescript
pi.on("before_agent_start", async (event) => {
  const block = await client.systemPromptBlock(agentId, maxChars);
  return { systemPrompt: event.systemPrompt + "\n\n" + block + "\n\n" + MEMORY_GUIDELINES };
});
```

### `input` — 显式保存意图拦截
```typescript
const SAVE_KEYWORDS = [
  "记住", "记下来", "别忘了", "不要忘记", "记一下", "帮我记", "保存这个", "记录下来",
  "remember this", "save this", "note that", "don't forget", "keep in mind",
];

pi.on("input", async (event) => {
  if (SAVE_KEYWORDS.some(kw => event.text.includes(kw))) {
    pi.appendEntry("memory-save-flag", { timestamp: Date.now() });
  }
  return { action: "continue" };
});
```

### `tool_call` — 追踪 Agent 是否已搜索
```typescript
let agentSearchedThisTurn = false;

pi.on("tool_call", (event) => {
  if (["memory_search", "memory_recall"].includes(event.toolName)) {
    agentSearchedThisTurn = true;
  }
});
```

### `context` — 兜底 prefetch（Agent 没搜时）
```typescript
const MEMORY_KEYWORDS = [
  "记得", "记住", "忘记", "之前", "上次", "以前", "说过", "聊过", "提到", "告诉过",
  "remember", "recall", "before", "previously", "mentioned",
];

pi.on("context", async (event) => {
  if (agentSearchedThisTurn) { agentSearchedThisTurn = false; return; }
  agentSearchedThisTurn = false;

  const userMsg = getLastUserMessage(event.messages);
  if (!userMsg || !MEMORY_KEYWORDS.some(kw => userMsg.includes(kw))) return;

  const results = await client.hybrid(userMsg, { limit: 3 });
  if (results.length > 0) {
    return { messages: [...event.messages, { role: "user", content: `[Memory:]\n${format(results)}` }] };
  }
});
```

### `turn_end` — 自动提取
```typescript
pi.on("turn_end", async (event) => {
  const content = extractText(event.message);
  if (!content || content.length < 50) return;
  try { await client.save(content.slice(0, 500), "auto-extract", [], "private", ""); }
  catch { /* best-effort */ }
});
```

### `session_shutdown` — 会话结束
```typescript
pi.on("session_shutdown", async () => {
  try { await client.save("Session ended", "session-end", ["session"], "private", ""); }
  catch { /* best-effort */ }
});
```

## 实施顺序

1. 更新 tool descriptions — 加入触发条件
2. 修改 `before_agent_start` — 加记忆指南
3. 添加 `tool_call` 事件 — 追踪 Agent 是否已搜索
4. 添加 `input` 事件 — 显式保存意图拦截
5. 添加 `context` 事件 — 兜底 prefetch
6. 实现 `turn_end` — 自动提取
7. 添加 `session_shutdown` — 会话结束

## Verification

- 说"你还记得xxx"，Agent 应该主动调 memory_search
- 说"记住这个：部署用SSH到10.0.0.5"，`input` 事件拦截标记
- Agent 没搜但用户提到"之前"、"上次"，`context` 兜底搜
- 对话结束后检查是否有 auto-extract 记忆
