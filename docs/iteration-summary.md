# 记忆系统迭代总结

## 本次迭代完成的功能

### 1. 记忆类型扩展
- 从 3 种类型扩展到 23 种
- 新增分类：user-*, feedback-*, project-*, learned-*, knowledge-*, reference-*
- 向后兼容旧类型（procedural, entity, knowledge）

### 2. RAG 文档系统
- **文档摄入**：单文件、目录批量、URL 抓取、Git 仓库
- **智能分块**：按 Markdown 标题分块，合并小块，拆分大块
- **混合检索**：FTS5 关键词 + sqlite-vec 向量 + RRF 融合
- **同步机制**：检测源文件变化，清理死链

### 3. 学习记忆系统
- **学习类型**：learned-research, learned-solution, learned-summary, learned-pattern
- **走正常生命周期**：raw → promoted → hot → archived（不再特殊晋升）
- **统一检索**：一次搜索结构化记忆 + 学习记忆 + RAG 文档

### 4. 巡检引擎
- **Phase 1：健康检查**
  - 记忆健康（过期、冲突、低质量）
  - RAG 同步（源文件变化、缺失）
  - 向量索引覆盖率监控
- **Phase 2：自主学习**
  - 从用户最近纠正的问题学习
  - 从知识空白学习
  - 深化用户兴趣领域
- **巡检日志**：记录每次巡检做了什么

### 5. 接口层
- **MCP Server**：18 个工具（memory_*, rag_*, patrol_*）
- **CLI**：rag, learn, patrol, unified 子命令
- **Web Dashboard**：巡检按钮 + 报告弹窗
- **Pi Agent 插件**：8 个新工具
- **Hermes 插件**：8 个新工具

## 简化的部分

### 去掉 learned → knowledge 特殊晋升
- **原因**：learned-* 已经走正常的 dreaming 生命周期，不需要额外的晋升机制
- **改动**：
  - memory_types.py：去掉 promote_to 配置
  - dreaming.py：去掉 _promote_learned_memories
  - db.py：去掉 get_learned_candidates_for_promotion, promote_memory
  - patrol.py：去掉 _check_learned_promotions

## 待优化

1. **学习内容质量评估**
   - 当前只是让 LLM 生成笔记，没有质量控制
   - 可以加入：用户反馈、引用验证、实用性评分

2. **巡检定时任务集成**
   - 需要与 dreaming scheduler 集成
   - 支持 cron 表达式配置

3. **RAG 文档版本管理**
   - 当前只检测内容变化，没有版本历史
   - 可以加入：版本对比、变更日志、回滚

4. **多 Agent 协作学习**
   - 多个 Agent 的学习内容可以共享
   - 避免重复学习相同主题

## 文件清单

| 文件 | 说明 |
|------|------|
| memory_types.py | 23 种记忆类型配置 |
| schema.py | RAG 文档表 |
| rag.py | RAG 引擎（摄入、检索、同步） |
| patrol.py | 巡检引擎（健康检查、自主学习） |
| db.py | RAG 操作、向量覆盖率查询 |
| store.py | 统一检索接口 |
| mcp_server.py | 18 个 MCP 工具 |
| cli.py | rag, learn, patrol, unified 命令 |
| providers/base.py | Hermes 插件工具 |
| extensions/pi-memory/ | Pi Agent 插件 |
| static/dashboard.html | Web 巡检界面 |
