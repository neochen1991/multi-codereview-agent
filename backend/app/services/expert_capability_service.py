from __future__ import annotations

from typing import Any

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.services.evidence_verifier_service import EvidenceVerifierService


class ExpertCapabilityService:
    """Builds execution context from expert capability metadata."""

    def __init__(self, verifier: EvidenceVerifierService | None = None) -> None:
        self.verifier = verifier or EvidenceVerifierService()
        self._supported_tools = {"local_diff", "coverage_diff", "schema_diff"}

    def collect_tool_evidence(
        self,
        expert: ExpertProfile,
        subject: ReviewSubject,
    ) -> list[dict[str, Any]]:
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
        sections = [
            f"职责重点: {self._join_or_default(expert.focus_areas, expert.role)}",
            f"触发线索: {self._join_or_default(expert.activation_hints, '按目标 diff 判定')}",
            f"必查清单: {self._join_or_default(expert.required_checks, '围绕代码证据执行最小充分审查')}",
            f"禁止越界: {self._join_or_default(expert.out_of_scope, '不要替其他专家下结论')}",
            f"证据来源: {self._join_or_default(expert.preferred_artifacts, 'diff hunk / 代码上下文 / 调用链')}",
            f"允许工具: {self._join_or_default(expert.tool_bindings, '无内建工具')}",
            f"知识来源: {self._join_or_default(expert.knowledge_sources, '无额外知识库')}",
            f"技能绑定: {self._join_or_default(expert.skill_bindings, '无附加 skill')}",
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
    ) -> str:
        matched = [
            hint
            for hint in expert.activation_hints
            if hint and hint.lower() in file_path.lower()
        ]
        if matched:
            return f"命中文件线索 {', '.join(matched[:3])}"
        if expert.focus_areas:
            return f"按职责优先检查 {expert.focus_areas[0]}"
        return f"按专家职责 {expert.role} 进行定向派工"

    def score_file_relevance(
        self,
        expert: ExpertProfile,
        file_path: str,
    ) -> int:
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

    def supported_tools(self, expert: ExpertProfile) -> list[str]:
        return [tool for tool in expert.tool_bindings if tool in self._supported_tools]

    def _join_or_default(self, values: list[str], default: str) -> str:
        return " / ".join(values) if values else default
