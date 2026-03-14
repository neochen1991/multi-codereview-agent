from __future__ import annotations

from typing import Any

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.services.evidence_verifier_service import EvidenceVerifierService


class ExpertCapabilityService:
    """Builds execution context from expert capability metadata."""

    def __init__(self, verifier: EvidenceVerifierService | None = None) -> None:
        """初始化工具探测器和支持的内建工具集合。"""

        self.verifier = verifier or EvidenceVerifierService()
        self._supported_tools = {"local_diff", "coverage_diff", "schema_diff"}

    def collect_tool_evidence(
        self,
        expert: ExpertProfile,
        subject: ReviewSubject,
    ) -> list[dict[str, Any]]:
        """执行专家显式绑定的内建工具，并返回预探测结果。"""

        evidence: list[dict[str, Any]] = []
        payload = {
            "changed_files": list(subject.changed_files),
            "unified_diff": subject.unified_diff,
            "metadata": dict(subject.metadata),
        }
        for tool_name in expert.tool_bindings:
            if tool_name not in self._supported_tools:
                continue
            result = self.verifier.verify(
                issue_id=f"tool_probe::{expert.expert_id}",
                strategy=tool_name,
                payload=payload,
            )
            evidence.append(result)
        return evidence

    def build_capability_summary(
        self,
        expert: ExpertProfile,
        tool_evidence: list[dict[str, Any]],
    ) -> str:
        """把专家能力元数据整理成供提示词注入的摘要文本。"""

        sections = [
            f"职责重点: {self._join_or_default(expert.focus_areas, expert.role)}",
            f"触发线索: {self._join_or_default(expert.activation_hints, '按目标 diff 判定')}",
            f"必查清单: {self._join_or_default(expert.required_checks, '围绕代码证据执行最小充分审查')}",
            f"禁止越界: {self._join_or_default(expert.out_of_scope, '不要替其他专家下结论')}",
            f"证据来源: {self._join_or_default(expert.preferred_artifacts, 'diff hunk / 代码上下文 / 调用链')}",
            f"允许工具: {self._join_or_default(expert.tool_bindings, '无内建工具')}",
            f"知识来源: {self._join_or_default(expert.knowledge_sources, '无额外知识库')}",
            f"运行时工具绑定: {self._join_or_default(expert.runtime_tool_bindings, '无附加运行时工具')}",
            f"MCP 工具: {self._join_or_default(expert.mcp_tools, '无 MCP 工具')}",
            f"协作对象: {self._join_or_default(expert.agent_bindings, 'judge')}",
        ]
        if tool_evidence:
            probe_summary = "；".join(
                f"{item['tool_name']}={'verified' if item.get('tool_verified') else 'not_matched'}({item.get('summary', '')})"
                for item in tool_evidence
            )
            sections.append(f"工具探测结果: {probe_summary}")
        return "\n".join(sections)

    def build_routing_reason(
        self,
        expert: ExpertProfile,
        file_path: str,
        hunk_excerpt: str = "",
        repo_context_excerpt: str = "",
    ) -> str:
        """根据文件、hunk 和源码上下文给出派工理由。"""

        lowered_file = file_path.lower()
        lowered_hunk = hunk_excerpt.lower()
        lowered_repo = repo_context_excerpt.lower()
        matched = [
            hint
            for hint in expert.activation_hints
            if hint and (hint.lower() in lowered_file or hint.lower() in lowered_hunk or hint.lower() in lowered_repo)
        ]
        if matched:
            return f"命中变更线索 {', '.join(matched[:3])}"
        if expert.focus_areas:
            return f"按职责优先检查 {expert.focus_areas[0]}"
        return f"按专家职责 {expert.role} 进行定向派工"

    def score_file_relevance(
        self,
        expert: ExpertProfile,
        file_path: str,
    ) -> int:
        """按文件路径粗粒度评估专家相关性。"""

        score = 0
        lowered = file_path.lower()
        for hint in expert.activation_hints:
            if hint.lower() in lowered:
                score += 4
        for area in expert.focus_areas:
            token = area.lower()
            if token and token in lowered:
                score += 2
        if expert.expert_id == "frontend_accessibility" and "frontend" in lowered:
            score += 3
        return score

    def score_hunk_relevance(
        self,
        expert: ExpertProfile,
        file_path: str,
        hunk_excerpt: str,
        repo_context_excerpt: str = "",
    ) -> int:
        """结合 hunk 内容和 repo context 细粒度评估专家相关性。"""

        score = self.score_file_relevance(expert, file_path)
        lowered = f"{file_path}\n{hunk_excerpt}\n{repo_context_excerpt}".lower()
        for hint in expert.activation_hints:
            token = hint.lower().strip()
            if token and token in lowered:
                score += 3
        for token in self._expert_signal_tokens(expert.expert_id):
            if token in lowered:
                score += 4
        for check in expert.required_checks:
            for token in self._extract_check_tokens(check):
                if token in lowered:
                    score += 1
        return score

    def supported_tools(self, expert: ExpertProfile) -> list[str]:
        """返回当前专家可使用且系统已支持的工具列表。"""

        return [tool for tool in expert.tool_bindings if tool in self._supported_tools]

    def _join_or_default(self, values: list[str], default: str) -> str:
        """把列表值拼成展示文本，不存在时返回默认说明。"""

        return " / ".join(values) if values else default

    def _expert_signal_tokens(self, expert_id: str) -> list[str]:
        """返回不同专家用于识别相关 hunk 的信号词。"""

        mapping = {
            "security_compliance": ["auth", "permission", "token", "security", "encrypt", "secret"],
            "database_analysis": ["select", "insert", "update", "delete", "sql", "schema", "migration", "index", "transaction"],
            "performance_reliability": ["timeout", "retry", "batch", "query", "cache", "migration", "rollback", "lock"],
            "redis_analysis": ["redis", "cache", "ttl", "expire", "setnx", "pipeline"],
            "mq_analysis": ["publish", "consumer", "producer", "queue", "kafka", "rabbit", "retry", "dead letter"],
            "test_verification": ["test", "expect", "assert", "spec", "it(", "describe("],
            "architecture_design": ["service", "repository", "module", "adapter", "domain", "application"],
            "correctness_business": ["return", "status", "state", "validate", "error", "if ", "null", "undefined"],
            "ddd_specification": ["aggregate", "domain", "entity", "value object", "repository", "application service"],
            "maintainability_code_health": ["todo", "if ", "switch", "else", "dup", "helper", "util"],
        }
        return mapping.get(expert_id, [])

    def _extract_check_tokens(self, check: str) -> list[str]:
        """从中文必查项中提取少量可用于匹配的关键词。"""

        normalized = str(check or "").lower()
        tokens: list[str] = []
        for token in ["事务", "锁", "回滚", "schema", "索引", "权限", "测试", "边界", "异常", "输入", "输出"]:
            if token in normalized:
                tokens.append(token)
        return tokens
