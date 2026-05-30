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

## 已完成的优化

### 6. 学习内容质量评估
- 评估维度：完整性、准确性、实用性、结构性
- LLM 自动评分，低于 0.6 分的内容不保存
- 评估结果附带在学习记录中

### 7. 巡检定时任务集成
- `--patrol-interval` 参数：设置巡检间隔（分钟）
- `--patrol-learning` 标志：巡检时包含自主学习
- 后台线程自动执行巡检

### 8. RAG 文档版本管理
- 版本历史表（rag_versions）
- 自动记录每次内容变更
- CLI 命令：`rag versions <doc_id>`、`rag diff <doc_id> v1 v2`
- 支持版本对比和变更追踪

## 待优化

1. **多 Agent 协作学习**
   - 多个 Agent 的学习内容可以共享
   - 避免重复学习相同主题

2. **RAG 文档回滚**
   - 支持回滚到历史版本
   - 恢复删除的文档

3. **学习内容引用验证**
   - 验证学习内容中的引用是否有效
   - 自动更新过期的引用

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
