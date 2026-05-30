"""Patrol Engine — 主动巡检 + 自主学习机制

Inspired by openhanako's heartbeat system.

Features:
  - 定期巡检记忆健康状态
  - RAG 文档同步
  - 自主学习（搜索、研究、整理）
  - 巡检日志记录

Two phases:
  Phase 1: 健康检查 + RAG 同步
  Phase 2: 自主学习（可选）
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

__all__ = ["PatrolEngine"]

logger = logging.getLogger("my-agent-memory.patrol")

# 巡检日志最大条目数
PATROL_LOG_MAX_ENTRIES = 100


class PatrolEngine:
    """主动巡检引擎 — 让记忆系统从被动存储变为主动管理"""

    def __init__(
        self,
        store,
        rag_engine=None,
        llm_client=None,
        patrol_dir: str = "",
    ):
        """
        Args:
            store: MultiAgentStore instance
            rag_engine: RAGEngine instance (optional)
            llm_client: LLMClient for self-learning (optional)
            patrol_dir: Directory for patrol logs and activity
        """
        self.store = store
        self.rag = rag_engine
        self.llm = llm_client

        # 巡检目录
        self.patrol_dir = Path(patrol_dir) if patrol_dir else Path.home() / ".hermes" / "patrol"
        self.patrol_dir.mkdir(parents=True, exist_ok=True)

        # 活动目录（自主学习产出）
        self.activity_dir = self.patrol_dir / "activity"
        self.activity_dir.mkdir(exist_ok=True)

        # 巡检日志
        self.patrol_log_path = self.patrol_dir / "patrol-log.md"

        # 指纹注册表（防重复）
        self.registry_path = self.patrol_dir / "registry.json"
        self._registry = self._load_registry()

    def patrol(self, include_learning: bool = False) -> dict:
        """执行一次完整巡检

        Args:
            include_learning: 是否包含自主学习阶段

        Returns:
            巡检报告
        """
        now = datetime.now(timezone.utc)
        report = {
            "timestamp": now.isoformat(),
            "phase1": {},
            "phase2": {},
            "actions": [],
            "summary": "",
        }

        try:
            # Phase 1: 健康检查 + RAG 同步
            logger.info("── 巡检开始 ──")
            report["phase1"] = self._phase1_health_check()
            report["actions"].extend(report["phase1"].get("actions", []))

            # Phase 2: 自主学习（可选）
            if include_learning and self.llm:
                report["phase2"] = self._phase2_learning()
                report["actions"].extend(report["phase2"].get("actions", []))

            # 生成摘要
            report["summary"] = self._generate_summary(report)

            # 写巡检日志
            self._write_patrol_log(report)

            logger.info(f"── 巡检完成: {report['summary']} ──")

        except Exception as e:
            logger.error(f"巡检错误: {e}")
            report["error"] = str(e)

        return report

    def _phase1_health_check(self) -> dict:
        """Phase 1: 记忆健康检查 + RAG 同步"""
        result = {
            "memory_health": {},
            "rag_health": {},
            "actions": [],
        }

        # 1. 记忆健康检查
        result["memory_health"] = self._check_memory_health()

        # 2. RAG 同步
        if self.rag:
            result["rag_health"] = self._check_rag_health()

        # 3. 过期记忆检查
        stale = result["memory_health"].get("stale_memories", [])
        if stale:
            result["actions"].append(f"发现 {len(stale)} 条过期记忆")

        # 4. 冲突检查
        conflicts = result["memory_health"].get("conflicts", [])
        if conflicts:
            result["actions"].append(f"发现 {len(conflicts)} 对冲突记忆")

        return result

    def _phase2_learning(self) -> dict:
        """Phase 2: 自主学习

        智能学习策略：
        1. 分析用户最近的问题 → 学习相关知识
        2. 分析用户代码风格 → 学习用户偏好
        3. 读取用户文档 → 学习项目上下文
        4. 检查知识空白 → 补充基础知识
        5. 自动分享高质量学习内容
        """
        result = {
            "strategies": [],
            "learnings": [],
            "shared": [],
            "actions": [],
        }

        if not self.llm:
            return result

        # 策略1: 从用户最近的问题学习
        recent_learnings = self._learn_from_recent_questions()
        if recent_learnings:
            result["strategies"].append("recent_questions")
            result["learnings"].extend(recent_learnings)
            result["actions"].append(f"从最近问题学习: {len(recent_learnings)} 个主题")

        # 策略2: 从知识空白学习（检查跨Agent共享）
        gap_learnings = self._learn_from_knowledge_gaps()
        if gap_learnings:
            result["strategies"].append("knowledge_gaps")
            result["learnings"].extend(gap_learnings)
            result["actions"].append(f"补充知识空白: {len(gap_learnings)} 个主题")

        # 策略3: 从用户兴趣深化
        deep_learnings = self._deepen_interests()
        if deep_learnings:
            result["strategies"].append("deepen_interests")
            result["learnings"].extend(deep_learnings)
            result["actions"].append(f"深化兴趣: {len(deep_learnings)} 个主题")

        # 自动分享高质量学习内容
        shared = self._auto_share_learnings(result["learnings"])
        if shared:
            result["shared"] = shared
            result["actions"].append(f"分享学习内容: {len(shared)} 个")

        return result

    def _auto_share_learnings(self, learnings: list) -> list:
        """自动分享高质量学习内容给其他Agent"""
        shared = []

        for learning in learnings:
            if not learning or not learning.get("entry_id"):
                continue

            quality = learning.get("quality", {})
            score = quality.get("score", 0)

            # 高质量内容（>=0.8分）自动分享
            if score >= 0.8:
                if self.share_learning(learning["entry_id"]):
                    shared.append({
                        "entry_id": learning["entry_id"],
                        "topic": learning.get("topic", ""),
                        "score": score,
                    })

        return shared

    def _learn_from_recent_questions(self) -> list:
        """从用户最近的问题中学习"""
        if not self.llm:
            return []

        # 获取最近的 feedback-correction 记忆（用户纠正过的问题）
        corrections = self.store.search(
            "纠正 错误 问题",
            limit=5,
            memory_type="feedback-correction",
        )

        learnings = []
        for correction in corrections[:2]:
            topic = correction.get("title", "")
            if topic:
                learning = self._generate_learning(f"用户曾纠正的问题: {topic}")
                if learning:
                    learnings.append(learning)

        return learnings

    def _learn_from_knowledge_gaps(self) -> list:
        """从知识空白中学习（检查跨Agent共享）"""
        if not self.llm:
            return []

        # 分析用户兴趣
        interests = self._analyze_interests()
        if not interests:
            return []

        # 找出知识空白（检查所有Agent的共享记忆）
        gaps = []
        for interest in interests[:10]:
            # 搜索所有Agent的共享记忆
            results = self.store.search(interest, limit=3, scope="shared")
            if len(results) < 2:
                # 也搜索当前Agent的私有记忆
                private_results = self.store.search(interest, limit=3, scope="private")
                if len(private_results) < 2:
                    gaps.append(interest)

        learnings = []
        for gap in gaps[:2]:  # 每次最多学习 2 个空白
            # 检查是否已有其他Agent学习过
            if self._is_already_learned_by_others(gap):
                logger.info(f"跳过学习，已有其他Agent学习过: {gap}")
                continue

            learning = self._generate_learning(gap)
            if learning:
                learnings.append(learning)

        return learnings

    def _is_already_learned_by_others(self, topic: str) -> bool:
        """检查主题是否已被其他Agent学习"""
        # 搜索共享的学习记忆
        results = self.store.search(
            topic,
            limit=3,
            scope="shared",
            memory_type="learned-*",
        )

        # 检查是否有其他Agent的学习记录
        for result in results:
            if result.get("owner_agent") != self.store.agent_id:
                return True

        return False

    def share_learning(self, entry_id: int) -> bool:
        """将学习内容分享给其他Agent"""
        try:
            result = self.store.share(entry_id)
            if result:
                logger.info(f"分享学习内容: {entry_id}")
                return True
        except Exception as e:
            logger.error(f"分享学习内容失败: {e}")
        return False

    def get_shared_learnings(self, limit: int = 10) -> list:
        """获取其他Agent分享的学习内容"""
        return self.store.search(
            "学习 笔记 总结",
            limit=limit,
            scope="shared",
            memory_type="learned-*",
        )

    def _deepen_interests(self) -> list:
        """深化用户兴趣领域的知识"""
        if not self.llm:
            return []

        # 获取用户最常用的标签
        tag_freq = self.store.db.get_tag_frequencies(limit=5)
        if not tag_freq:
            return []

        # 选择一个标签深入学习
        top_tag = tag_freq[0].get("tag", "")
        if not top_tag:
            return []

        # 生成进阶学习内容
        learning = self._generate_learning(f"{top_tag} 进阶技巧")
        return [learning] if learning else []

    def _check_memory_health(self) -> dict:
        """检查记忆健康状态"""
        stats = self.store.stats()
        total = stats.get("total", 0)

        # 检查过期记忆（90天未访问）
        stale = self._find_stale_memories(days=90)

        # 检查冲突
        conflicts = self.store.get_conflicts(status="open")

        # 检查低质量记忆
        low_quality = self._find_low_quality()

        # 检查向量索引覆盖率
        vector_coverage = self._check_vector_coverage()

        return {
            "total": total,
            "stale_memories": stale,
            "conflicts": conflicts,
            "low_quality": low_quality,
            "vector_coverage": vector_coverage,
            "by_state": stats.get("by_state", {}),
            "by_type": stats.get("by_type", {}),
        }

    def _check_vector_coverage(self) -> dict:
        """检查向量索引覆盖率"""
        total = self.store.db.fetchone(
            "SELECT COUNT(*) as cnt FROM memory_entries WHERE deleted_at IS NULL"
        )["cnt"]

        with_embedding = self.store.db.fetchone(
            "SELECT COUNT(*) as cnt FROM memory_entries WHERE embedding IS NOT NULL AND deleted_at IS NULL"
        )["cnt"]

        coverage = (with_embedding / total * 100) if total > 0 else 0

        return {
            "total": total,
            "with_embedding": with_embedding,
            "coverage_percent": round(coverage, 1),
        }

    def _check_rag_health(self) -> dict:
        """检查 RAG 健康状态"""
        if not self.rag:
            return {"status": "not_available"}

        # 同步检查
        sync_result = self.rag.sync(remove_orphans=False)

        return {
            "total_documents": sync_result.get("total", 0),
            "valid": sync_result.get("valid", 0),
            "missing": sync_result.get("missing", 0),
            "outdated": sync_result.get("updated", 0),
        }

    def _check_learned_promotions(self) -> list:
        """检查学习记忆晋升"""
        return []

    def _find_stale_memories(self, days: int = 90) -> list:
        """查找过期记忆"""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.store.db.fetchall(
            """SELECT id, title, memory_type, last_access_ts, access_count
               FROM memory_entries
               WHERE state != 'archived'
                 AND deleted_at IS NULL
                 AND is_pinned = 0
                 AND (last_access_ts IS NULL OR last_access_ts < ?)
               ORDER BY last_access_ts ASC
               LIMIT 50""",
            (cutoff,),
        )
        return [dict(r) for r in rows]

    def _find_low_quality(self) -> list:
        """查找低质量记忆"""
        rows = self.store.db.fetchall(
            """SELECT id, title, memory_type, LENGTH(content) as content_len
               FROM memory_entries
               WHERE state != 'archived'
                 AND deleted_at IS NULL
                 AND LENGTH(content) < 20
               ORDER BY LENGTH(content) ASC
               LIMIT 20""",
        )
        return [dict(r) for r in rows]

    def _analyze_interests(self) -> list:
        """分析用户兴趣领域"""
        # 从标签频率分析
        tag_freq = self.store.db.get_tag_frequencies(limit=20)
        return [t["tag"] for t in tag_freq if t.get("tag")]

    def _find_knowledge_gaps(self, interests: list) -> list:
        """找出知识空白"""
        if not interests:
            return []

        # 检查每个兴趣领域有多少记忆
        gaps = []
        for interest in interests[:10]:
            results = self.store.search(interest, limit=3)
            if len(results) < 2:
                gaps.append(interest)

        return gaps[:5]

    def _generate_learning(self, topic: str) -> Optional[dict]:
        """生成学习内容并评估质量"""
        if not self.llm:
            return None

        try:
            # 生成学习内容
            prompt = f"""请针对以下主题生成一份简洁的学习笔记（200-500字）：

主题：{topic}

要求：
1. 概述主题的核心概念
2. 列出 3-5 个关键点
3. 给出实际应用场景
4. 使用 Markdown 格式

请直接输出笔记内容，不要添加额外说明。"""

            content = self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1000,
            )

            if not content:
                return None

            # 评估内容质量
            quality = self._assess_learning_quality(content, topic)

            # 只保存质量合格的内容
            if quality.get("score", 0) < 0.6:
                logger.info(f"学习内容质量不足: {topic} (score: {quality.get('score', 0)})")
                return None

            # 保存学习内容
            entry = self.store.save(
                content=content,
                title=f"学习笔记: {topic}",
                tags=["学习笔记", topic],
                memory_type="learned-summary",
            )

            # 保存到活动目录
            file_path = self.activity_dir / f"learn-{hashlib.md5(topic.encode()).hexdigest()[:8]}.md"
            file_path.write_text(f"# {topic}\n\n{content}", encoding="utf-8")

            return {
                "topic": topic,
                "entry_id": entry.get("id"),
                "file": str(file_path),
                "quality": quality,
            }

        except Exception as e:
            logger.error(f"生成学习内容失败: {e}")
            return None

    def _assess_learning_quality(self, content: str, topic: str) -> dict:
        """评估学习内容质量

        评估维度：
        1. 完整性：是否涵盖核心概念
        2. 准确性：是否有明显错误
        3. 实用性：是否有实际应用场景
        4. 结构性：是否有清晰的结构
        """
        if not self.llm:
            return {"score": 0.8, "reason": "无 LLM，跳过评估"}

        try:
            prompt = f"""请评估以下学习内容的质量（0-1分）：

主题：{topic}

内容：
{content[:500]}

评估维度：
1. 完整性（0.3分）：是否涵盖核心概念
2. 准确性（0.3分）：是否有明显错误
3. 实用性（0.2分）：是否有实际应用场景
4. 结构性（0.2分）：是否有清晰的结构

请返回 JSON 格式：
{{"score": 0.85, "reason": "评估原因", "dimensions": {{"completeness": 0.9, "accuracy": 0.8, "practicality": 0.7, "structure": 0.8}}}}"""

            response = self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )

            if response:
                import json
                try:
                    return json.loads(response)
                except json.JSONDecodeError:
                    pass

        except Exception as e:
            logger.error(f"质量评估失败: {e}")

        # 默认返回及格分数
        return {"score": 0.7, "reason": "评估失败，使用默认分数"}

    def _generate_summary(self, report: dict) -> str:
        """生成巡检摘要"""
        parts = []

        # Phase 1 摘要
        p1 = report.get("phase1", {})
        mh = p1.get("memory_health", {})
        if mh.get("total"):
            parts.append(f"记忆: {mh['total']} 条")

        stale = mh.get("stale_memories", [])
        if stale:
            parts.append(f"过期: {len(stale)}")

        conflicts = mh.get("conflicts", [])
        if conflicts:
            parts.append(f"冲突: {len(conflicts)}")

        # 向量覆盖率
        vc = mh.get("vector_coverage", {})
        if vc.get("coverage_percent") is not None:
            parts.append(f"向量: {vc['coverage_percent']}%")

        # RAG 摘要
        rh = p1.get("rag_health", {})
        if rh.get("total_documents"):
            parts.append(f"RAG: {rh['total_documents']} 文档")
            if rh.get("missing"):
                parts.append(f"缺失: {rh['missing']}")

        # Phase 2 摘要
        p2 = report.get("phase2", {})
        learnings = p2.get("learnings", [])
        if learnings:
            parts.append(f"学习: {len(learnings)}")

        return " | ".join(parts) if parts else "无变化"

    def _write_patrol_log(self, report: dict):
        """写巡检日志"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- [{now}] {report.get('summary', '巡检完成')}"

        # 追加到日志文件
        try:
            existing = ""
            if self.patrol_log_path.exists():
                existing = self.patrol_log_path.read_text(encoding="utf-8")

            # 截断旧日志
            lines = existing.split("\n") if existing else []
            entries = [l for l in lines if l.startswith("- [")]
            if len(entries) >= PATROL_LOG_MAX_ENTRIES:
                entries = entries[-PATROL_LOG_MAX_ENTRIES:]

            entries.append(entry)
            self.patrol_log_path.write_text("\n".join(entries) + "\n", encoding="utf-8")

        except Exception as e:
            logger.error(f"写巡检日志失败: {e}")

    def _load_registry(self) -> dict:
        """加载指纹注册表"""
        try:
            if self.registry_path.exists():
                return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_registry(self):
        """保存指纹注册表"""
        try:
            self.registry_path.write_text(
                json.dumps(self._registry, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"保存注册表失败: {e}")

    def get_patrol_log(self, limit: int = 20) -> list:
        """获取巡检日志"""
        try:
            if not self.patrol_log_path.exists():
                return []

            content = self.patrol_log_path.read_text(encoding="utf-8")
            lines = [l.strip() for l in content.split("\n") if l.strip().startswith("- [")]
            return lines[-limit:]

        except Exception:
            return []

    def get_activity_files(self) -> list:
        """获取活动目录文件"""
        try:
            return [
                f.name for f in self.activity_dir.iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
        except Exception:
            return []
