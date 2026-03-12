from __future__ import annotations

import os

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.expert_capability_service import ExpertCapabilityService
from app.services.llm_chat_service import LLMChatService, LLMTextResult


class MainAgentService:
    agent_id = "main_agent"
    agent_name = "MainAgent"

    def __init__(self) -> None:
        self._llm = LLMChatService()
        self._diff_excerpt_service = DiffExcerptService()
        self._capability_service = ExpertCapabilityService()

    def build_command(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        runtime_settings: RuntimeSettings,
    ) -> dict[str, object]:
        resolution = self._llm.resolve_main_agent(runtime_settings)
        file_path = self._pick_file_path(subject, expert)
        line_start = self._pick_line_start(subject, expert.expert_id, file_path)
        routing_reason = self._capability_service.build_routing_reason(expert, file_path)
        fallback_text = self._build_command_fallback(subject, expert, file_path, line_start)
        user_prompt = (
            f"待审查对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"目标专家: {expert.expert_id} / {expert.name_zh}\n"
            f"专家职责: {expert.role}\n"
            f"聚焦文件: {file_path}\n"
            f"起始行号: {line_start}\n"
            f"派工理由: {routing_reason}\n"
            f"变更文件: {', '.join(subject.changed_files[:5]) or '未提供'}\n"
            f"必查项: {' / '.join(expert.required_checks) or expert.role}\n"
            f"禁止越界: {' / '.join(expert.out_of_scope) or '不要替其他专家下最终结论'}\n"
            f"允许工具: {' / '.join(expert.tool_bindings) or '无'}\n"
            f"请用中文输出一条明确的派工指令，必须点名该专家，说明为什么看这个文件/行，"
            f"明确要求其给出代码证据、问题倾向、修复建议，并提醒其不要评论超出职责边界的内容。"
        )
        llm_result = self._llm.complete_text(
            system_prompt=(
                "你是主代码审查协调Agent。你的职责是把具体审查任务分配给明确的专家，"
                "要求他们围绕指定文件和行号给出可验证证据。"
                "你必须做定向派工，不要给泛化任务，不要让专家越界。"
            ),
            user_prompt=user_prompt,
            resolution=resolution,
            fallback_text=fallback_text,
            allow_fallback=self._allow_fallback(runtime_settings),
        )
        return {
            "expert_id": expert.expert_id,
            "expert_name": expert.name_zh,
            "file_path": file_path,
            "line_start": line_start,
            "summary": llm_result.text.strip(),
            "llm": self._llm_metadata(llm_result),
        }

    def build_final_summary(
        self,
        review: ReviewTask,
        issues: list[DebateIssue],
        runtime_settings: RuntimeSettings,
    ) -> tuple[str, dict[str, object]]:
        blocker_count = len([issue for issue in issues if issue.severity in {"blocker", "critical"}])
        pending_count = len([issue for issue in issues if issue.needs_human and issue.status != "resolved"])
        resolution = self._llm.resolve_main_agent(runtime_settings)
        fallback_text = (
            f"主Agent收敛完成：本次共形成 {len(issues)} 个议题，其中 blocker/critical {blocker_count} 个，"
            f"待人工裁决 {pending_count} 个，审核状态 {review.status}，下一步请优先处理高风险结论。"
        )
        user_prompt = (
            f"审核状态: {review.status}\n"
            f"审核阶段: {review.phase}\n"
            f"议题总数: {len(issues)}\n"
            f"高风险议题数: {blocker_count}\n"
            f"待人工裁决数: {pending_count}\n"
            f"请输出一段中文总结，风格像主Agent对控制台的收敛播报。"
        )
        result = self._llm.complete_text(
            system_prompt="你是主代码审查协调Agent，负责在多专家完成分析后输出最终的收敛播报。",
            user_prompt=user_prompt,
            resolution=resolution,
            fallback_text=fallback_text,
            allow_fallback=self._allow_fallback(runtime_settings),
        )
        return result.text.strip(), self._llm_metadata(result)

    def _allow_fallback(self, runtime_settings: RuntimeSettings) -> bool:
        return bool(runtime_settings.allow_llm_fallback or os.getenv("PYTEST_CURRENT_TEST"))

    def _build_command_fallback(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
    ) -> str:
        focus = expert.focus_areas[0] if expert.focus_areas else expert.role
        return (
            f"@{expert.expert_id}，请你从“{focus}”视角先审查 {file_path} 第 {line_start} 行附近的改动。"
            f" 这部分变更与 {subject.target_ref or '主分支'} 基线存在偏移风险，"
            f"请先说明为什么这段代码值得你来查，再给出代码证据、问题倾向和修复建议。"
        )

    def _pick_file_path(self, subject: ReviewSubject, expert: ExpertProfile) -> str:
        changed_files = list(subject.changed_files)
        if not changed_files:
            return "src/review/runtime.py"
        ranked = sorted(
            changed_files,
            key=lambda item: self._capability_service.score_file_relevance(expert, item),
            reverse=True,
        )
        if ranked and self._capability_service.score_file_relevance(expert, ranked[0]) > 0:
            return ranked[0]
        expert_id = expert.expert_id
        if expert_id == "security_compliance":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["auth", "security", "permission", "token"]):
                    return file_path
        if expert_id in {"performance_reliability", "compatibility_change_impact"}:
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["migration", "sql", "schema", "db", "repository"]):
                    return file_path
        if expert_id == "database_analysis":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["migration", "sql", "schema", "db", "repository", "dao"]):
                    return file_path
        if expert_id == "redis_analysis":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["redis", "cache"]):
                    return file_path
        if expert_id == "mq_analysis":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["mq", "kafka", "rocketmq", "rabbit", "queue", "consumer", "producer"]):
                    return file_path
        if expert_id == "ddd_specification":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["domain", "aggregate", "entity", "repository", "service", "application"]):
                    return file_path
        if expert_id == "test_verification":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["test", "spec", "playwright", "jest", "vitest"]):
                    return file_path
        return changed_files[0]

    def _pick_line_start(self, subject: ReviewSubject, expert_id: str, file_path: str) -> int:
        preferred_line = 12
        if expert_id == "security_compliance":
            preferred_line = 18
        elif expert_id == "architecture_design":
            preferred_line = 42
        elif expert_id == "performance_reliability":
            preferred_line = 57
        elif expert_id == "database_analysis":
            preferred_line = 36
        elif expert_id == "redis_analysis":
            preferred_line = 28
        elif expert_id == "mq_analysis":
            preferred_line = 30
        elif expert_id == "ddd_specification":
            preferred_line = 40
        elif expert_id == "test_verification":
            preferred_line = 73

        return self._diff_excerpt_service.find_nearest_line(
            subject.unified_diff,
            file_path,
            preferred_line,
        ) or preferred_line

    def _llm_metadata(self, result: LLMTextResult) -> dict[str, object]:
        return {
            "provider": result.provider,
            "model": result.model,
            "base_url": result.base_url,
            "api_key_env": result.api_key_env,
            "mode": result.mode,
            "error": result.error,
        }
