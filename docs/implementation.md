# Hermes Memory v2 — 实现与集成方案

> 2026-05-22 · 待 noor/hanako 确认

---

## 1. 交付物

| 交付物 | 形式 | 说明 |
|--------|------|------|
| `hermes-memory-v2` | pip 包 | Python SDK + CLI，发布到 npm registry mirror |
| v1 兼容层 | 包内 `Store` 类 | noor 零改动升级 |
| HanakoProvider | 包内 `providers/hanako.py` | hanako 零改动接入 |
| CLI | `hermes-memory` 命令 | Kilo / OpenClaw / 调试用 |
| 迁移脚本 | CLI 子命令 | v1 → v2 数据迁移 |

**不交付**：不修改 noor 源码，不修改 hanako 源码，不要求 Kilo 装 Python。

---

## 2. noor 集成：包升级，零源码改动

### 2.1 原理

v1 的 noor 代码这样用记忆：

```python
from hermes_memory import Store
store = Store()
store.search("腾讯云服务器")
store.save("新事实", title="标题")
```

v2 做的兼容层：

```python
# hermes_memory_v2 包的 __init__.py 里
# 提供同名的 Store 类，签名完全兼容

class Store:
    def __init__(self, db_path: str = ""):
        agent_id = os.getenv("HERMES_AGENT_ID", "noor")  # 从环境变量读
        self._store = MultiAgentStore(db_path=db_path, agent_id=agent_id)

    def search(self, query, limit=10, offset=0, tags=None, source=None):
        return self._store.search(query, limit, offset, tags, source=source)

    def save(self, content, title="", tags=None, source="manual"):
        return self._store.save(content, title, tags, source)

    # get(), archive(), status(), dream(), rebuild() 同理包装
```

### 2.2 noor 侧操作

```
# 1. 停掉现有 cron
crontab -l | grep memory-dreaming  # 找到并暂停

# 2. 升级包
pip install hermes-memory-v2

# 3. 设环境变量
export HERMES_AGENT_ID=noor  # 加到 profile

# 4. 迁移数据
hermes-memory migrate \
  --v1-db E:/hermes/hermes-data/memories/memory.db \
  --v1-hot E:/hermes/hermes-data/memories \
  --agent noor \
  --execute

# 5. 恢复 cron
# dreaming 命令不变，底层已切换到 v2
```

**改动清单**：零源码改动。停 cron → 升级包 → 设变量 → 迁移 → 恢复 cron。

---

## 3. hanako 集成：外部 Provider 扩展点

### 3.0 现状（已确认）

hanako 源码中 MemoryProvider ABC 和目录扫描**尚未实现**。hanako 的 `memory-manager` 插件只说"支持外部记忆服务"，但代码里没有 `agent/memory_provider.py`、没有 ABC、没有 provider 发现机制。

已有的是目录扫描模式（`scanAgentDirs`、`scanExternalSkills`），这个模式可以复用。

### 3.1 分两步完成

**第一步：hanako 侧建立扩展点**（hanako 源码改动）

```python
# agent/memory_provider.py（新增文件）

class MemoryProvider(ABC):
    """外部记忆源接口，由插件包实现"""
    @abstractmethod
    def prefetch(self, query: str) -> str: ...
    @abstractmethod
    def system_prompt_block(self) -> str: ...
    @abstractmethod
    def sync(self, user_msg: str, assistant_msg: str) -> None: ...
    @abstractmethod
    def on_session_end(self) -> None: ...


# agent/memory_manager.py（改动：加目录扫描 + 注册逻辑）

def _scan_external_providers(hermes_home: Path) -> list[MemoryProvider]:
    """复用现有 scanAgentDirs 模式，扫描 providers/ 目录"""
    providers_dir = hermes_home / "providers"
    if not providers_dir.is_dir():
        return []
    providers = []
    for config_file in sorted(providers_dir.glob("*.json")):
        config = json.loads(config_file.read_text())
        provider = _load_provider(config)
        if provider:
            providers.append(provider)
    return providers
```

改动量估算：~50 行新代码 + 5 行调整 MemoryManager 初始化流程。不是"为 v2 打补丁"——hanako 设计文档自己承诺了 `builtin + 一个外部 provider`，这是补齐承诺的架构能力。v2 只是第一个消费者。

**第二步：v2 侧实现 HanakoProvider**（v2 包内，不影响 hanako 源码）

v2 包内置 `hermes_memory_v2/providers/hanako.py`，实现第一步定义的 `MemoryProvider` ABC。

```
hanako 源码（完全不动）              v2 包
─────────────────────              ─────
agent/memory_provider.py           hermes_memory_v2/providers/hanako.py
  MemoryProvider (ABC)               HanakoProvider(MemoryProvider)
  ├─ system_prompt_block()    ←──      → 返回 shared + 自己的 hot 层内容
  ├─ prefetch(query)          ←──      → 调用 hybrid_search()
  ├─ sync(user, asst)         ←──      → 持久化到 SQLite
  └─ on_session_end()         ←──      → 触发 dreaming 收敛

agent/memory_manager.py (不动)
  → 扫描 providers/ 目录加载配置
  → 加载到的 HanakoProvider 注册为 external
```

### 3.2 HanakoProvider 实现（v2 包内）

```python
# hermes_memory_v2/providers/hanako.py

from agent.memory_provider import MemoryProvider  # 依赖第一步的 ABC

class HanakoProvider(MemoryProvider):
    def __init__(self, config):
        self.store = MultiAgentStore(
            db_path=config["db_path"],
            agent_id=config.get("agent_id", "hanako"),
        )

    def system_prompt_block(self) -> str:
        """返回 hot 层内容，注入 hanako 系统 prompt 的 volatile 层"""
        return self.store.get_system_prompt_block(
            agent_id="hanako",
            include_shared=True,
        )

    def prefetch(self, query: str) -> str:
        """每轮对话前语义搜索，结果 append 到消息上下文"""
        results = self.store.hybrid_search(query, limit=5)
        if not results:
            return ""
        lines = []
        for r in results:
            lines.append(f"- {r['title']}: {r['content'][:120]}")
        return "\n".join(lines)

    def sync(self, user_msg: str, assistant_msg: str):
        """每轮后 hanako 的本地经验库自己管，Hermes 侧暂不同步"""
        pass

    def on_session_end(self):
        """session 结束时触发 dreaming"""
        self.store.dreaming(dry_run=False)
```

### 3.3 部署步骤

```
# 前提：hanako 已合入 MemoryProvider ABC + 目录扫描（第一步）

# 1. 安装 v2 包
pip install hermes-memory-v2

# 2. 放配置文件（在 hermes_home 下，不在源码目录，不会被覆盖）
mkdir -p $HERMES_HOME/providers
cat > $HERMES_HOME/providers/hermes_v2.json <<'EOF'
{
  "module": "hermes_memory_v2.providers.hanako",
  "class": "HanakoProvider",
  "agent_id": "hanako",
  "db_path": "E:/hermes/hermes-data/memories/memory_v2.db"
}
EOF

# 3. 重启 hanako session，MemoryManager 自动扫描并加载
```

**改动清单**：hanako 源码加 ~50 行（MemoryProvider ABC + 目录扫描），v2 包内 80 行（HanakoProvider 实现），用户 1 个 JSON 配置文件。配置文件在 `HERMES_HOME` 下，不在源码仓库，不会被 git 覆盖。

---

## 4. Kilo 集成：CLI Subprocess

Kilo 没有 Python runtime，通过 `hermes-memory` CLI 工具接入。

```
# session 启动时，获取 hot 层快照注入 system prompt
hermes-memory hot-snapshot --agent kilo --format markdown

# 需要搜索记忆时
hermes-memory hybrid "用户 API key 在哪" --agent kilo --scope shared --limit 5
```

Node.js 侧封装：

```typescript
// Kilo 记忆工具
async function recallMemory(query: string): Promise<string> {
  const { stdout } = await exec(
    `hermes-memory hybrid "${query}" --agent kilo --scope shared --limit 5`
  );
  return stdout;
}
```

Kilo 不是 Hermes agent，不参与 dreaming 生产——只消费 hot 层快照和 hybrid 搜索。

---

## 5. 数据迁移

所有 agent 共享同一个 `memory_v2.db`，迁移只执行一次。

```
before:                               after:
──────                               ─────
E:/hermes/hermes-data/memories/       E:/hermes/hermes-data/memories/
  memory.db                            memory_v1_backup.db (保留不动)
  MEMORY.md                            memory_v2.db (新建)
  USER.md                              shared/
  server.md                              MEMORY.md (空，待填充)
  preferences.md                       noor/
  ...                                    MEMORY.md (从 v1 迁移)
                                         USER.md
```

```bash
hermes-memory migrate \
  --v1-db E:/hermes/hermes-data/memories/memory.db \
  --v1-hot E:/hermes/hermes-data/memories \
  --agent noor \
  --dry-run    # 先预览

hermes-memory migrate \
  --v1-db E:/hermes/hermes-data/memories/memory.db \
  --v1-hot E:/hermes/hermes-data/memories \
  --agent noor \
  --execute    # 执行
```

---

## 6. 部署顺序

```
Phase 1: 开发 + 测试
  1. 完成 hermes-memory-v2 包
  2. 完成兼容层 Store 类
  3. 完成 HanakoProvider
  4. 完成迁移脚本 + dry-run 验证

Phase 2: noor 先切（风险最低）
  1. pip install hermes-memory-v2
  2. 设 HERMES_AGENT_ID=noor
  3. 执行迁移
  4. 验证 dreaming 正常

Phase 3: hanako 后切（等 noor 跑稳 1-2 天）
  1. pip install hermes-memory-v2
  2. 放 provider 配置文件
  3. 重启 session 验证

Phase 4: Kilo 接入（随时）
  1. CLI 命令可用即可接入
```

---

## 7. 风险与回退

| 风险 | 缓解 |
|------|------|
| 迁移破坏 v1 数据 | 迁移脚本不改 v1 文件，只读 + 新建 |
| noor 兼容层有 bug | pip uninstall hermes-memory-v2 → pip install hermes-memory（v1 包不变） |
| hanako provider 加载失败 | 删掉配置文件，MemoryManager 回退到 builtin-only 模式 |
| 两个 agent 并发写 SQLite | WAL 模式并发读安全，写操作短事务，SQLite 自带锁 |

**回退时间**：任一 agent 出问题，5 分钟内可回到 v1。

---

## 8. 待确认

| # | 问题 | 状态 |
|---|------|------|
| 1 | hanako 接受 MemoryProvider ABC + 目录扫描（~50 行）作为功能增强 | **待 hanako 确认** |
| 2 | noor 的 dreaming cron 是否由 cron 调度？v2 保持原样 | 中 |
| 3 | hanako 侧 MemoryProvider ABC 定义在哪个文件？`agent/memory_provider.py` 还是 `tools/memory_tool.py` 内 | 低——实现时定 |
