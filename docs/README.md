# My Agent Memory — 文档索引

## 核心文档

| 文件 | 内容 | 读者 |
|------|------|------|
| [architecture.md](./architecture.md) | 系统架构设计：领域模型、存储设计、生命周期、多 Agent 协议、API 契约 | 所有人 |
| [implementation.md](./implementation.md) | 实现与集成方案：各 agent 接入路径、部署顺序、迁移步骤 | noor / hanako / Kilo |
| [review.md](./review.md) | 审议记录：butter / hanako / ming 三轮讨论 + noor 独立评估 | 历史参考 |

## 参考文档

| 文件 | 内容 |
|------|------|
| [prompt.md](./prompt.md) | 原始设计任务书，描述需求和设计范围 |
| [v1-hermes.md](./v1-hermes.md) | Hermes v1 记忆架构（noor 当前使用的系统） |
| [v1-hanako.md](./v1-hanako.md) | Hanako 记忆架构（§ 分隔文本 + frozen snapshot） |

## 项目结构

```
my-agent-memory/
├── docs/                          # 文档（当前目录）
├── src/my_agent_memory/           # Python 包
│   ├── __init__.py                # MultiAgentStore, Store 导出
│   ├── store.py                   # 顶层 API + v1 兼容 Store
│   ├── db.py                      # SQLite schema + FTS5 + sqlite-vec
│   ├── search.py                  # FTS5 + 向量 + RRF 融合搜索
│   ├── scoring.py                 # 评分公式
│   ├── embed.py                   # SiliconFlow embedding
│   ├── dreaming.py                # 生命周期引擎
│   ├── conflicts.py               # 冲突检测与解决
│   ├── validate.py                # 注入扫描
│   ├── hot_layer.py               # Markdown 投影生成
│   ├── llm.py                     # DeepSeek Chat 客户端
│   ├── migrate.py                 # v1 → v2 迁移
│   ├── cli.py                     # CLI
│   ├── serve.py                   # 管理页面 HTTP 服务
│   ├── provider.py                # MemoryProvider ABC
│   ├── providers/
│   │   └── hanako.py              # Hanako Python provider
│   └── static/
│       └── dashboard.html         # React 管理页面
├── js/
│   └── hermes-provider.js         # Hanako JS wrapper（CLI subprocess 桥接）
├── tests/
│   └── test_v1_compat.py          # v1 API 兼容性测试
└── pyproject.toml                 # pip 包定义
```

## 快速开始

```powershell
# 安装
pip install -e E:\hermes\project\my-agent-memory

# 设环境变量
$env:HERMES_AGENT_ID = "noor"

# 启动管理页面
my-agent-memory serve --port 8765

# CLI 搜索
my-agent-memory search "关键词"
my-agent-memory hybrid "语义搜索"
```

## 多 Agent 接入

| Agent | 方式 | 改动量 |
|-------|------|--------|
| **noor** | `from my_agent_memory import Store`，API 兼容 v1 | 零源码改动 |
| **hanako** | 配置文件 `hermes_v2.json` → JS wrapper → CLI subprocess | 零源码改动 |
| **Kilo** | `my-agent-memory` CLI subprocess | 新接入 |
