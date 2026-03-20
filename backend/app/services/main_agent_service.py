from __future__ import annotations

import json
import os
import re
from typing import Literal

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.expert_capability_service import ExpertCapabilityService
from app.services.llm_chat_service import LLMChatService, LLMTextResult
from app.services.repository_context_service import RepositoryContextService


class MainAgentService:
    """主 Agent 协调器。

    它本身不直接产出最终 finding，而是负责：
    - 在专家执行前做派工规划
    - 在审核结束后做全局收敛播报
    """

    agent_id = "main_agent"
    agent_name = "MainAgent"
    TEST_PATH_MARKERS = {"test", "tests", "__tests__", "__mocks__", "spec", "specs", "fixtures", "playwright", "cypress"}

    def __init__(self) -> None:
        self._llm = LLMChatService()
        self._diff_excerpt_service = DiffExcerptService()
        self._capability_service = ExpertCapabilityService()
        self._repo_context_cache: dict[tuple[str, str, str, tuple[str, ...]], dict[str, object]] = {}

    def build_command(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        runtime_settings: RuntimeSettings,
        route_hint: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """为单个专家生成一次带上下文的派工指令。"""
        change_chain = self.build_change_chain(subject)
        repository_service = self._build_repository_service(runtime_settings)
        target_focus = route_hint or self._build_rule_route(subject, expert, repository_service)
        file_path = str(target_focus.get("file_path") or self._pick_file_path(subject, expert))
        line_start = (
            int(target_focus.get("line_start") or self._pick_line_start(subject, expert.expert_id, file_path))
            if file_path
            else 0
        )
        target_hunk = dict(target_focus.get("target_hunk") or {})
        related_files = self._build_expert_related_files(subject, expert, file_path, change_chain["related_files"])
        routing_repo_excerpt = self._format_repo_matches(dict(target_focus.get("repo_hits") or {}))
        routing_reason = str(target_focus.get("routing_reason") or "").strip() or self._capability_service.build_routing_reason(
            expert,
            file_path,
            str(target_hunk.get("excerpt") or ""),
            routing_repo_excerpt,
        )
        expected_checks = self._build_expected_checks(expert, change_chain)
        disallowed_inference = self._build_disallowed_inference(expert)
        repo_context = self._build_repository_context(
            repository_service,
            file_path,
            line_start,
            related_files,
            dict(target_focus.get("repo_hits") or {}),
        )
        routeable, skip_reason = self._should_route_expert(subject, expert, target_focus, file_path)
        summary = self._build_command_fallback(
            subject,
            expert,
            file_path,
            line_start,
            target_hunk=target_hunk,
            routing_reason=routing_reason,
            expected_checks=expected_checks,
            disallowed_inference=disallowed_inference,
            related_files=related_files,
        )
        return {
            "expert_id": expert.expert_id,
            "expert_name": expert.name_zh,
            "file_path": file_path,
            "line_start": line_start,
            "related_files": related_files,
            "target_hunk": target_hunk,
            "repository_context": repo_context,
            "expected_checks": expected_checks,
            "disallowed_inference": disallowed_inference,
            "routeable": routeable,
            "skip_reason": skip_reason,
            "routing_reason": routing_reason,
            "routing_confidence": float(target_focus.get("confidence") or 0.0),
            "summary": summary,
            "llm": {
                "provider": "main-agent-template",
                "model": "template",
                "base_url": "",
                "api_key_env": "",
                "mode": "template",
                "error": "",
            },
        }

    def build_routing_plan(
        self,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        runtime_settings: RuntimeSettings,
        *,
        analysis_mode: Literal["standard", "light"] = "standard",
    ) -> dict[str, dict[str, object]]:
        """基于完整业务变更和专家职责生成统一派工计划。

        流程分两层：
        1. 先用现有规则挑出每个专家的 baseline route，作为兜底计划
        2. 再把全部业务变更、候选 hunk 和专家职责交给 LLM 做语义分工
        """
        self._repo_context_cache.clear()
        repository_service = self._build_repository_service(runtime_settings)
        baseline_routes = {
            expert.expert_id: self._build_rule_route(subject, expert, repository_service)
            for expert in experts
        }
        if analysis_mode == "light":
            for route in baseline_routes.values():
                route["routing_llm"] = {
                    "provider": "main-agent-template",
                    "model": "template",
                    "base_url": "",
                    "api_key_env": "",
                    "mode": "rule_only_light",
                    "error": "",
                }
            return baseline_routes
        candidate_hunks = self._build_candidate_hunks(subject, repository_service)
        if not candidate_hunks or not experts:
            return baseline_routes

        fallback_payload = self._build_routing_plan_payload(
            subject=subject,
            experts=experts,
            routes=baseline_routes,
            candidate_hunks=candidate_hunks,
        )
        resolution = self._llm.resolve_main_agent(runtime_settings)
        result = self._llm.complete_text(
            system_prompt=self._build_routing_system_prompt(),
            user_prompt=self._build_routing_user_prompt(
                subject=subject,
                experts=experts,
                candidate_hunks=candidate_hunks,
            ),
            resolution=resolution,
            runtime_settings=runtime_settings,
            fallback_text=json.dumps(fallback_payload, ensure_ascii=False),
            allow_fallback=self._allow_fallback(runtime_settings),
            timeout_seconds=self._main_agent_timeout_seconds(runtime_settings),
            max_attempts=self._main_agent_max_attempts(runtime_settings),
            log_context={
                "phase": "routing_plan",
                "agent_id": self.agent_id,
                "source_ref": subject.source_ref,
                "target_ref": subject.target_ref,
                "changed_file_count": len(subject.changed_files),
            },
        )
        parsed = self._parse_routing_plan(result.text)
        merged = self._merge_routing_plan(
            subject=subject,
            experts=experts,
            baseline_routes=baseline_routes,
            candidate_hunks=candidate_hunks,
            llm_plan=parsed,
        )
        for route in merged.values():
            route["routing_llm"] = self._llm_metadata(result)
        return merged

    def select_review_experts(
        self,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        runtime_settings: RuntimeSettings,
        *,
        requested_expert_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """让主 Agent 基于 MR 信息与专家画像决定本次真正参与审核的专家。"""
        if not experts:
            return {
                "selected_expert_ids": [],
                "selected_experts": [],
                "skipped_experts": [],
                "candidate_expert_ids": [],
                "requested_expert_ids": list(requested_expert_ids or []),
                "llm": self._llm_metadata(
                    LLMTextResult(
                        text="",
                        mode="template",
                        provider="main-agent-template",
                        model="template",
                        base_url="",
                        api_key_env="",
                    )
                ),
            }
        candidate_expert_ids = [expert.expert_id for expert in experts]
        fallback_ids = [
            expert_id
            for expert_id in list(requested_expert_ids or [])
            if expert_id in candidate_expert_ids
        ] or candidate_expert_ids
        fallback_payload = {
            "selected_experts": [
                {
                    "expert_id": expert_id,
                    "reason": "fallback_selected",
                    "confidence": 0.5,
                }
                for expert_id in fallback_ids
            ],
            "skipped_experts": [],
        }
        resolution = self._llm.resolve_main_agent(runtime_settings)
        result = self._llm.complete_text(
            system_prompt=self._build_expert_selection_system_prompt(),
            user_prompt=self._build_expert_selection_user_prompt(
                subject=subject,
                experts=experts,
                requested_expert_ids=list(requested_expert_ids or []),
            ),
            resolution=resolution,
            runtime_settings=runtime_settings,
            fallback_text=json.dumps(fallback_payload, ensure_ascii=False),
            allow_fallback=self._allow_fallback(runtime_settings),
            timeout_seconds=self._main_agent_timeout_seconds(runtime_settings),
            max_attempts=self._main_agent_max_attempts(runtime_settings),
            log_context={
                "phase": "expert_selection",
                "agent_id": self.agent_id,
                "source_ref": subject.source_ref,
                "target_ref": subject.target_ref,
                "changed_file_count": len(subject.changed_files),
            },
        )
        parsed = self._parse_json_payload(result.text)
        merged = self._merge_expert_selection(
            experts=experts,
            requested_expert_ids=list(requested_expert_ids or []),
            llm_payload=parsed,
            fallback_ids=fallback_ids,
        )
        merged["llm"] = self._llm_metadata(result)
        return merged

    def build_change_chain(self, subject: ReviewSubject) -> dict[str, object]:
        """基于 changed_files 推导一条粗粒度的关联变更链。"""
        changed_files = [item for item in subject.changed_files if item]
        business_files = [item for item in changed_files if not self._is_test_like_path(item)]
        if business_files:
            changed_files = business_files
        related_files: list[str] = []
        token_links = {
            "migration": ["schema", "repository", "service", "transform", "output"],
            "schema": ["migration", "repository", "service", "transform", "output"],
            "service": ["transform", "output", "schema", "migration"],
            "transform": ["service", "output", "schema", "migration"],
            "output": ["service", "transform", "schema", "migration"],
            "repository": ["schema", "migration", "service"],
        }
        lowered = {path: path.lower() for path in changed_files}
        for path in changed_files:
            if path not in related_files:
                related_files.append(path)
            current = lowered[path]
            for token, candidates in token_links.items():
                if token not in current:
                    continue
                for candidate_path, candidate_lowered in lowered.items():
                    if candidate_path in related_files:
                        continue
                    if any(candidate in candidate_lowered for candidate in candidates):
                        related_files.append(candidate_path)
        return {
            "primary_files": changed_files[:2],
            "related_files": related_files or changed_files[:],
        }

    def build_intake_summary(self, subject: ReviewSubject) -> tuple[str, dict[str, object]]:
        """把远程平台返回的审核输入整理成主 Agent 的前置播报。"""
        metadata = dict(subject.metadata or {})
        business_changed_files = self._candidate_changed_files(subject, "")
        review_url = str(subject.mr_url or metadata.get("review_url") or subject.repo_url or "").strip()
        platform_kind = str(metadata.get("platform_kind") or metadata.get("platform_provider") or "代码平台").strip()
        summary = (
            f"已接收 {platform_kind} 的审核输入：{subject.title or review_url or subject.source_ref}。"
            f" 当前识别到 {len(subject.changed_files)} 个变更文件，其中业务文件 {len(business_changed_files)} 个。"
        )
        if not subject.changed_files:
            summary = "未从远程代码平台获取到真实 diff 或变更文件，后续派工可能受限。"
        return summary, {
            "title": subject.title,
            "review_url": review_url,
            "platform_kind": platform_kind,
            "source_ref": subject.source_ref,
            "target_ref": subject.target_ref,
            "compare_mode": str(metadata.get("compare_mode") or "").strip(),
            "remote_diff_fetched": bool(metadata.get("remote_diff_fetched")),
            "changed_files": list(subject.changed_files),
            "business_changed_files": business_changed_files,
        }

    def build_final_summary(
        self,
        review: ReviewTask,
        issues: list[DebateIssue],
        runtime_settings: RuntimeSettings,
        *,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
    ) -> tuple[str, dict[str, object]]:
        """让主 Agent 在 issue 收敛后输出控制台播报式总结。"""
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
            runtime_settings=runtime_settings,
            fallback_text=fallback_text,
            allow_fallback=self._allow_fallback(runtime_settings),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            log_context={
                "review_id": review.review_id,
                "agent_id": self.agent_id,
                "phase": "final_summary",
                "analysis_mode": review.analysis_mode,
            },
        )
        return result.text.strip(), self._llm_metadata(result)

    def _allow_fallback(self, runtime_settings: RuntimeSettings) -> bool:
        return bool(runtime_settings.allow_llm_fallback or os.getenv("PYTEST_CURRENT_TEST"))

    def _main_agent_timeout_seconds(self, runtime_settings: RuntimeSettings) -> float:
        """主 Agent 的专家选择/派工 prompt 更长，优先采用运行时中的较大超时。"""

        standard_timeout = float(getattr(runtime_settings, "standard_llm_timeout_seconds", 60) or 60)
        light_timeout = float(getattr(runtime_settings, "light_llm_timeout_seconds", 120) or 120)
        return float(max(60.0, standard_timeout, light_timeout))

    def _main_agent_max_attempts(self, runtime_settings: RuntimeSettings) -> int:
        """主 Agent 遇到内网抖动时，优先使用当前运行时允许的较大重试次数。"""

        standard_retries = int(getattr(runtime_settings, "standard_llm_retry_count", 3) or 3)
        light_retries = int(getattr(runtime_settings, "light_llm_retry_count", 2) or 2)
        return max(1, standard_retries, light_retries)

    def _build_command_fallback(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        *,
        target_hunk: dict[str, object] | None = None,
        routing_reason: str = "",
        expected_checks: list[str] | None = None,
        disallowed_inference: list[str] | None = None,
        related_files: list[str] | None = None,
    ) -> str:
        focus = expert.focus_areas[0] if expert.focus_areas else expert.role
        checks_text = " / ".join(list(expected_checks or [])[:4]) or focus
        disallowed_text = " / ".join(list(disallowed_inference or [])[:3]) or "不要越界评论"
        related_text = " / ".join(list(related_files or [])[:4]) or "无"
        hunk_header = str((target_hunk or {}).get("hunk_header") or "未定位到明确 hunk")
        return (
            f"**派工指令**\n\n"
            f"**目标专家：** {expert.expert_id} / {expert.name_zh}\n\n"
            f"**审查对象：** {subject.title or subject.mr_url or subject.source_ref}\n\n"
            f"**定向任务：** 请聚焦文件 `{file_path}` 第 **{line_start} 行** 附近的变更，"
            f"重点从“{focus}”视角审查。\n\n"
            f"**目标 hunk：** {hunk_header}\n"
            f"**派工理由：** {routing_reason or f'该变更与 {focus} 风险直接相关'}\n"
            f"**关联文件：** {related_text}\n"
            f"**必查项：** {checks_text}\n"
            f"**禁止推断：** {disallowed_text}\n\n"
            f"请明确给出：1. 代码证据 2. 问题倾向 3. 修复建议。"
        )

    def _build_expected_checks(self, expert: ExpertProfile, change_chain: dict[str, object]) -> list[str]:
        checks = list(expert.required_checks)
        if change_chain.get("related_files") and "跨文件一致性" not in checks:
            checks.append("跨文件一致性")
        return checks[:6]

    def _build_disallowed_inference(self, expert: ExpertProfile) -> list[str]:
        rules = [
            "不要仅凭 import 变化断言未完成需求",
            "不要仅凭命名猜测权限缺陷",
            "证据不足时只能输出待验证风险",
        ]
        rules.extend(expert.out_of_scope)
        deduped: list[str] = []
        for item in rules:
            if item and item not in deduped:
                deduped.append(item)
        return deduped[:6]

    def _pick_file_path(self, subject: ReviewSubject, expert: ExpertProfile) -> str:
        changed_files = self._candidate_changed_files(subject, expert.expert_id)
        if not changed_files:
            return ""
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

    def _pick_target_focus(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        repository_service: RepositoryContextService,
    ) -> dict[str, object]:
        changed_files = self._candidate_changed_files(subject, expert.expert_id)
        if not changed_files:
            return {}
        if expert.expert_id == "correctness_business":
            preferred = self._pick_correctness_chain_focus(subject, repository_service)
            if preferred:
                return preferred
        substantive_hunks_present = self._review_has_substantive_hunks(subject, changed_files)
        best_candidate: dict[str, object] | None = None
        best_score = -1
        for file_path in self._ordered_changed_files(subject, expert, changed_files):
            hunks = self._diff_excerpt_service.list_hunks(subject.unified_diff, file_path)
            if not hunks:
                fallback_line = self._pick_line_start(subject, expert.expert_id, file_path)
                hunks = [
                    {
                        "file_path": file_path,
                        "hunk_header": "",
                        "start_line": fallback_line,
                        "end_line": fallback_line,
                        "changed_lines": [fallback_line],
                        "excerpt": self._diff_excerpt_service.extract_excerpt(subject.unified_diff, file_path, fallback_line),
                    }
                ]
            for hunk in hunks:
                if substantive_hunks_present and self._is_low_signal_hunk(str(hunk.get("excerpt") or "")):
                    continue
                repo_hits = self._search_related_repo_context(repository_service, file_path, hunk)
                score = self._capability_service.score_hunk_relevance(
                    expert,
                    file_path,
                    str(hunk.get("excerpt") or ""),
                    self._format_repo_matches(repo_hits),
                )
                if self._is_import_only_hunk(str(hunk.get("excerpt") or "")):
                    score -= 6
                if self._is_format_only_hunk(str(hunk.get("excerpt") or "")):
                    score -= 8
                if score <= best_score:
                    continue
                changed_lines = [int(item) for item in list(hunk.get("changed_lines") or []) if isinstance(item, int)]
                line_start = changed_lines[0] if changed_lines else int(hunk.get("start_line") or 1)
                best_candidate = {
                    "file_path": file_path,
                    "line_start": line_start,
                    "target_hunk": hunk,
                    "repo_hits": repo_hits,
                    "score": score,
                }
                best_score = score
        return best_candidate or {}

    def _build_rule_route(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        repository_service: RepositoryContextService,
    ) -> dict[str, object]:
        target_focus = self._pick_target_focus(subject, expert, repository_service)
        file_path = str(target_focus.get("file_path") or self._pick_file_path(subject, expert))
        line_start = (
            int(target_focus.get("line_start") or self._pick_line_start(subject, expert.expert_id, file_path))
            if file_path
            else 0
        )
        target_hunk = dict(target_focus.get("target_hunk") or {})
        routing_reason = self._capability_service.build_routing_reason(
            expert,
            file_path,
            str(target_hunk.get("excerpt") or ""),
            self._format_repo_matches(dict(target_focus.get("repo_hits") or {})),
        )
        routeable, skip_reason = self._should_route_expert(subject, expert, target_focus, file_path)
        return {
            **target_focus,
            "expert_id": expert.expert_id,
            "file_path": file_path,
            "line_start": line_start,
            "target_hunk": target_hunk,
            "routing_reason": routing_reason,
            "routeable": routeable,
            "skip_reason": skip_reason,
            "confidence": 0.55 if routeable else 0.25,
            "routing_source": "rule",
        }

    def _pick_correctness_chain_focus(
        self,
        subject: ReviewSubject,
        repository_service: RepositoryContextService,
    ) -> dict[str, object]:
        changed_files = [item for item in subject.changed_files if item]
        for file_path in changed_files:
            lowered = file_path.lower()
            if "transform" not in lowered:
                continue
            hunks = self._diff_excerpt_service.list_hunks(subject.unified_diff, file_path)
            for hunk in hunks:
                excerpt = str(hunk.get("excerpt") or "")
                if not any(token in excerpt for token in ["createdAt", "updatedAt", "override"]):
                    continue
                changed_lines = [int(item) for item in list(hunk.get("changed_lines") or []) if isinstance(item, int)]
                line_start = changed_lines[0] if changed_lines else int(hunk.get("start_line") or 1)
                repo_hits = self._search_related_repo_context(repository_service, file_path, hunk)
                return {
                    "file_path": file_path,
                    "line_start": line_start,
                    "target_hunk": hunk,
                    "repo_hits": repo_hits,
                    "score": 100,
                }
        return {}

    def _ordered_changed_files(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        changed_files: list[str],
    ) -> list[str]:
        if expert.expert_id != "correctness_business":
            return changed_files

        def priority(path: str) -> tuple[int, int]:
            lowered = path.lower()
            excerpt = self._diff_excerpt_service.extract_excerpt(subject.unified_diff, path, 1)
            score = 99
            if "transform" in lowered:
                score = 0
            elif "output" in lowered:
                score = 1
            elif "service" in lowered:
                score = 2
            elif "schema" in lowered or "migration" in lowered:
                score = 3
            if any(token in excerpt for token in ["createdAt", "updatedAt", "override"]):
                score -= 1
            return score, len(path)

        return sorted(changed_files, key=priority)

    def _pick_line_start(self, subject: ReviewSubject, expert_id: str, file_path: str) -> int:
        if not file_path:
            return 0
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

    def _build_expert_related_files(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        related_files: list[str],
    ) -> list[str]:
        if not file_path:
            return []
        business_changed_files = self._candidate_changed_files(subject, expert.expert_id)
        if expert.expert_id != "correctness_business":
            merged: list[str] = []
            for path in [file_path, *business_changed_files, *related_files]:
                normalized = str(path).strip()
                if normalized and normalized not in merged:
                    merged.append(normalized)
            return merged
        chain_tokens = ("transform", "output", "service", "schema", "migration")
        prioritized: list[str] = []
        for path in business_changed_files:
            lowered = path.lower()
            if any(token in lowered for token in chain_tokens) and path not in prioritized:
                prioritized.append(path)
        for path in related_files:
            if path not in prioritized:
                prioritized.append(path)
        if file_path in prioritized:
            prioritized.remove(file_path)
        return [file_path, *prioritized]

    def _should_route_expert(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        target_focus: dict[str, object],
        file_path: str,
    ) -> tuple[bool, str]:
        if not str(file_path).strip():
            return False, "未获取到真实 diff，无法为该专家定位待审查代码"
        score = int(target_focus.get("score") or 0)
        target_hunk = dict(target_focus.get("target_hunk") or {})
        import_only = self._is_import_only_hunk(str(target_hunk.get("excerpt") or ""))
        expert_id = expert.expert_id
        lowered = file_path.lower()
        excerpt_lowered = str(target_hunk.get("excerpt") or "").lower()
        global_diff_lowered = self._strip_non_diff_signal_lines(str(subject.unified_diff or "")).lower()
        changed_files_lowered = "\n".join(subject.changed_files).lower()
        global_blob = "\n".join([lowered, excerpt_lowered, changed_files_lowered, global_diff_lowered])

        if expert_id == "mq_analysis" and not any(token in f"{lowered}\n{excerpt_lowered}" for token in ["mq", "queue", "kafka", "rabbit", "consumer", "producer"]):
            return False, "当前变更未命中该中间件专家的关键线索"
        if expert_id == "redis_analysis" and not any(token in f"{lowered}\n{excerpt_lowered}" for token in ["redis", "cache", "ttl", "expire", "setnx", "pipeline"]):
            return False, "当前变更未命中该缓存专家的关键线索"
        if expert_id == "security_compliance" and not any(
            token in global_blob
            for token in [
                "auth",
                "security",
                "permission",
                "token",
                "secret",
                "frame",
                "decoder",
                "encode",
                "netty",
                "memory",
                "oom",
                "payload",
                "dos",
                "denial",
                "serialize",
            ]
        ):
            return False, "当前变更未命中安全相关线索"
        if expert_id == "frontend_accessibility" and "frontend" not in lowered:
            return False, "当前变更不属于前端可访问性审查范围"
        if expert_id in {"ddd_specification", "architecture_design", "maintainability_code_health", "security_compliance"} and import_only:
            return False, "当前 hunk 仅为 import 级调整，缺少足够的结构性审查信号"
        return True, ""

    def _strip_non_diff_signal_lines(self, unified_diff: str) -> str:
        kept_lines: list[str] = []
        for line in str(unified_diff or "").splitlines():
            if line.startswith(("diff --git ", "@@ ", "@@", "--- ", "+++ ", "+", "-")):
                kept_lines.append(line)
        return "\n".join(kept_lines)

    def _is_import_only_hunk(self, excerpt: str) -> bool:
        if not excerpt.strip():
            return False
        changed_lines: list[str] = []
        for line in excerpt.splitlines():
            cleaned = self._extract_changed_line(line)
            if cleaned.startswith("# "):
                continue
            if not cleaned.startswith(("+", "-")):
                continue
            if cleaned in {"+", "-"}:
                continue
            changed_lines.append(cleaned)
        return bool(changed_lines) and all(line.startswith(("+import", "-import")) for line in changed_lines)

    def _is_format_only_hunk(self, excerpt: str) -> bool:
        if not excerpt.strip():
            return False
        added: list[str] = []
        removed: list[str] = []
        for line in excerpt.splitlines():
            cleaned = self._extract_changed_line(line)
            cleaned = cleaned.rstrip()
            if cleaned.startswith("# "):
                continue
            stripped = cleaned.lstrip()
            if stripped.startswith("+"):
                added.append(stripped[1:])
            elif stripped.startswith("-"):
                removed.append(stripped[1:])
        if not added or not removed or len(added) != len(removed):
            if not added or not removed:
                return False

        def normalize(value: str) -> str:
            return re.sub(r"\s+", "", value)

        return "".join(normalize(item) for item in added) == "".join(normalize(item) for item in removed)

    def _extract_changed_line(self, line: str) -> str:
        prefixed_match = re.match(r"^\s*(?P<prefix>[+-])\s*\|\s?(?P<body>.*)$", line)
        if prefixed_match:
            return f"{prefixed_match.group('prefix')}{prefixed_match.group('body')}".strip()
        numbered_match = re.match(r"^\s*\d+\s*\|\s?(?P<body>[+-].*)$", line)
        if numbered_match:
            return numbered_match.group("body").strip()
        return line.strip()

    def _is_low_signal_hunk(self, excerpt: str) -> bool:
        return self._is_import_only_hunk(excerpt) or self._is_format_only_hunk(excerpt)

    def _review_has_substantive_hunks(self, subject: ReviewSubject, changed_files: list[str]) -> bool:
        for file_path in changed_files:
            for hunk in self._diff_excerpt_service.list_hunks(subject.unified_diff, file_path):
                if not self._is_low_signal_hunk(str(hunk.get("excerpt") or "")):
                    return True
        return False

    def _llm_metadata(self, result: LLMTextResult) -> dict[str, object]:
        return {
            "provider": result.provider,
            "model": result.model,
            "base_url": result.base_url,
            "api_key_env": result.api_key_env,
            "mode": result.mode,
            "error": result.error,
        }

    def _build_repository_context(
        self,
        service: RepositoryContextService,
        file_path: str,
        line_start: int,
        related_files: list[str],
        repo_hits: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not str(file_path).strip():
            return {
                "summary": "未获取到真实 diff，暂无代码仓上下文",
                "primary_context": {},
                "related_contexts": [],
                "search_matches": [],
                "context_files": [],
            }
        if not service.is_ready():
            return {
                "summary": "代码仓上下文未配置或本地仓不可用",
                "primary_context": {},
                "related_contexts": [],
                "search_matches": [],
                "context_files": [],
            }
        primary_context = service.load_file_context(file_path, line_start, radius=10)
        repo_hit_matches = list((repo_hits or {}).get("matches", []) or [])
        repo_hit_paths = [
            str(item.get("path") or "").strip()
            for item in repo_hit_matches
            if (
                str(item.get("path") or "").strip()
                and str(item.get("path") or "").strip() != file_path
                and service.is_searchable_path(str(item.get("path") or "").strip())
                and not self._is_test_like_path(str(item.get("path") or "").strip())
            )
        ]
        related_contexts = [
            service.load_file_context(item, 1, radius=8)
            for item in [*related_files, *repo_hit_paths]
            if item != file_path and not self._is_test_like_path(item)
        ]
        context_files: list[str] = []
        for item in [file_path, *related_files, *repo_hit_paths]:
            normalized = str(item).strip()
            if (
                normalized
                and service.is_searchable_path(normalized)
                and not self._is_test_like_path(normalized)
                and normalized not in context_files
            ):
                context_files.append(normalized)
        return {
            "summary": (
                f"已补充 {len(context_files)} 个目标分支文件上下文，"
                f"并命中 {len(repo_hit_matches)} 条关联代码检索结果"
            ),
            "primary_context": primary_context,
            "related_contexts": related_contexts,
            "search_matches": repo_hit_matches,
            "symbol_contexts": list((repo_hits or {}).get("symbol_contexts", []) or []),
            "context_files": context_files,
        }

    def _candidate_changed_files(self, subject: ReviewSubject, expert_id: str) -> list[str]:
        changed_files = [item for item in subject.changed_files if item]
        if expert_id == "test_verification":
            test_files = [item for item in changed_files if self._is_test_like_path(item)]
            return test_files or changed_files
        business_files = [item for item in changed_files if not self._is_test_like_path(item)]
        return business_files or changed_files

    def _is_test_like_path(self, path: str) -> bool:
        parts = str(path or "").replace("\\", "/").split("/")
        return any(part.lower() in self.TEST_PATH_MARKERS for part in parts)

    def _build_repository_service(self, runtime_settings: RuntimeSettings) -> RepositoryContextService:
        return RepositoryContextService(
            clone_url=runtime_settings.code_repo_clone_url,
            local_path=runtime_settings.code_repo_local_path,
            default_branch=runtime_settings.code_repo_default_branch or runtime_settings.default_target_branch,
            access_token=runtime_settings.code_repo_access_token,
            auto_sync=runtime_settings.code_repo_auto_sync,
        )

    def _search_related_repo_context(
        self,
        service: RepositoryContextService,
        file_path: str,
        hunk: dict[str, object],
    ) -> dict[str, object]:
        if not service.is_ready():
            return {"queries": [], "matches": [], "symbol_contexts": []}
        queries = self._derive_repo_queries(file_path, hunk)
        if not queries:
            return {"queries": [], "matches": [], "symbol_contexts": []}
        cache_key = (
            str(service.local_path),
            service.default_branch,
            file_path,
            tuple(sorted(queries[:4])),
        )
        cached = self._repo_context_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        search_result = service.search_many(queries, globs=None, limit_per_query=4, total_limit=8)
        filtered_matches = [
            item
            for item in list(search_result.get("matches", []) or [])
            if not self._is_test_like_path(str(item.get("path") or ""))
        ]
        symbol_contexts = [
            service.search_symbol_context(query, globs=None, definition_limit=2, reference_limit=3)
            for query in queries[:3]
        ]
        filtered_symbol_contexts = []
        for context in symbol_contexts:
            filtered_symbol_contexts.append(
                {
                    **context,
                    "definitions": [
                        item
                        for item in list(context.get("definitions", []) or [])
                        if not self._is_test_like_path(str(item.get("path") or ""))
                    ],
                    "references": [
                        item
                        for item in list(context.get("references", []) or [])
                        if not self._is_test_like_path(str(item.get("path") or ""))
                    ],
                }
            )
        result = {
            **search_result,
            "matches": filtered_matches,
            "symbol_contexts": filtered_symbol_contexts,
        }
        self._repo_context_cache[cache_key] = dict(result)
        return result

    def _derive_repo_queries(self, file_path: str, hunk: dict[str, object]) -> list[str]:
        tokens: list[str] = []
        excerpt = str(hunk.get("excerpt") or "")
        for pattern in [
            r"(?:function|class|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?(?:\(|[A-Za-z_])",
            r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:async\s*)?\(",
            r"([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        ]:
            for match in re.findall(pattern, excerpt):
                if match.lower() in {"diff", "const", "return", "true", "false", "null", "none", "if", "for"}:
                    continue
                if match not in tokens:
                    tokens.append(match)
                if len(tokens) >= 4:
                    return tokens[:4]
        return tokens[:6]

    def _build_candidate_hunks(
        self,
        subject: ReviewSubject,
        repository_service: RepositoryContextService,
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        changed_files = self._candidate_changed_files(subject, "")
        substantive_hunks_present = self._review_has_substantive_hunks(subject, changed_files)
        for file_path in changed_files:
            hunks = self._diff_excerpt_service.list_hunks(subject.unified_diff, file_path)
            if not hunks:
                fallback_line = self._pick_line_start(subject, "", file_path)
                hunks = [
                    {
                        "file_path": file_path,
                        "hunk_header": "",
                        "start_line": fallback_line,
                        "end_line": fallback_line,
                        "changed_lines": [fallback_line],
                        "excerpt": self._diff_excerpt_service.extract_excerpt(subject.unified_diff, file_path, fallback_line),
                    }
                ]
            enriched_hunks: list[dict[str, object]] = []
            for hunk in hunks[:3]:
                excerpt = str(hunk.get("excerpt") or "")
                enriched_hunks.append(
                    {
                        "hunk": hunk,
                        "import_only": self._is_import_only_hunk(excerpt),
                        "format_only": self._is_format_only_hunk(excerpt),
                    }
                )
            has_substantive_hunk = any(
                not item["import_only"] and not item["format_only"] for item in enriched_hunks
            )
            filtered_hunks = [
                item
                for item in enriched_hunks
                if (
                    not has_substantive_hunk
                    or (not item["import_only"] and not item["format_only"])
                )
            ]
            for index, item in enumerate(filtered_hunks, start=1):
                if substantive_hunks_present and (item["import_only"] or item["format_only"]):
                    continue
                hunk = dict(item["hunk"])
                changed_lines = [int(item) for item in list(hunk.get("changed_lines") or []) if isinstance(item, int)]
                line_start = changed_lines[0] if changed_lines else int(hunk.get("start_line") or 1)
                repo_hits = self._search_related_repo_context(repository_service, file_path, hunk)
                candidates.append(
                    {
                        "candidate_id": f"{file_path}:{line_start}:{index}",
                        "file_path": file_path,
                        "line_start": line_start,
                        "hunk_header": str(hunk.get("hunk_header") or ""),
                        "excerpt": str(hunk.get("excerpt") or ""),
                        "import_only": bool(item["import_only"]),
                        "format_only": bool(item["format_only"]),
                        "repo_hits": repo_hits,
                    }
                )
        return candidates

    def _build_routing_plan_payload(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        routes: dict[str, dict[str, object]],
        candidate_hunks: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "expert_routes": [
                {
                    "expert_id": expert.expert_id,
                    "file_path": str(routes.get(expert.expert_id, {}).get("file_path") or ""),
                    "line_start": int(routes.get(expert.expert_id, {}).get("line_start") or 0),
                    "candidate_id": self._match_candidate_id(candidate_hunks, routes.get(expert.expert_id, {})),
                    "routeable": bool(routes.get(expert.expert_id, {}).get("routeable", True)),
                    "reason": str(routes.get(expert.expert_id, {}).get("routing_reason") or ""),
                    "confidence": float(routes.get(expert.expert_id, {}).get("confidence") or 0.0),
                }
                for expert in experts
            ],
            "skipped_experts": [
                {
                    "expert_id": expert.expert_id,
                    "reason": str(routes.get(expert.expert_id, {}).get("skip_reason") or ""),
                }
                for expert in experts
                if not bool(routes.get(expert.expert_id, {}).get("routeable", True))
            ],
        }

    def _build_routing_system_prompt(self) -> str:
        return (
            "你是多专家代码审查系统的主Agent，职责是根据完整代码变更和专家职责进行派工。"
            "请只输出 JSON，不要输出任何解释性文字。"
            "派工原则：1. 优先依据代码语义和变更内容，而不是路径；"
            "2. 非 test_verification 专家默认避开 test/spec 文件；"
            "3. 每个专家只选择一个主焦点 hunk，但后续会收到完整业务变更信息；"
            "4. 若当前变更与专家职责不符，可以 routeable=false 并给出 skip reason；"
            "5. 输出必须使用提供的 candidate_id 或 file_path+line_start 对应真实候选 hunk。"
        )

    def _build_expert_selection_system_prompt(self) -> str:
        return (
            "你是多专家代码审查系统的主Agent。"
            "在正式派工前，你需要先根据 MR 信息、完整 diff 和专家画像，决定本次真正需要参与审核的专家集合。"
            "请只输出 JSON，不要输出解释。"
            "选择原则：1. 必须依据真实变更内容和专家职责边界选择；"
            "2. 专家数量应尽量精简，只保留真正相关的专家；"
            "3. 非前端改动不要选择前端专家；非安全线索不要强行选择安全专家；"
            "4. 变更涉及跨文件契约、业务逻辑、结构设计时，应优先保留正确性/架构/可维护性等通用专家；"
            "5. 如果某专家不需要参与，写入 skipped_experts 并说明原因；"
            "6. selected_experts 至少返回 1 个。"
        )

    def _build_routing_user_prompt(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        candidate_hunks: list[dict[str, object]],
    ) -> str:
        expert_sections = []
        for expert in experts:
            expert_sections.append(
                "\n".join(
                    [
                        f"- expert_id: {expert.expert_id}",
                        f"  名称: {expert.name_zh}",
                        f"  职责重点: {' / '.join(expert.focus_areas) or expert.role}",
                        f"  触发线索: {' / '.join(expert.activation_hints) or '按代码语义判断'}",
                        f"  必查项: {' / '.join(expert.required_checks) or '无'}",
                        f"  禁止越界: {' / '.join(expert.out_of_scope) or '无'}",
                    ]
                )
            )
        candidate_sections = []
        for item in candidate_hunks:
            candidate_sections.append(
                "\n".join(
                    [
                        f"- candidate_id: {item['candidate_id']}",
                        f"  file_path: {item['file_path']}",
                        f"  line_start: {item['line_start']}",
                        f"  hunk_header: {item['hunk_header']}",
                        f"  excerpt: {str(item['excerpt'])[:700]}",
                        f"  repo_context: {self._format_repo_matches(dict(item.get('repo_hits') or {}))[:500]}",
                    ]
                )
            )
        business_changed_files = self._candidate_changed_files(subject, "")
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"源分支: {subject.source_ref}\n"
            f"目标分支: {subject.target_ref}\n"
            f"全部变更文件: {json.dumps(list(subject.changed_files), ensure_ascii=False)}\n"
            f"业务变更文件: {json.dumps(business_changed_files, ensure_ascii=False)}\n"
            f"完整 diff:\n{subject.unified_diff[:12000]}\n\n"
            f"可用专家:\n{chr(10).join(expert_sections)}\n\n"
            f"候选 hunk:\n{chr(10).join(candidate_sections)}\n\n"
            "请输出 JSON，格式为：\n"
            "{\n"
            '  "expert_routes": [\n'
            "    {\n"
            '      "expert_id": "correctness_business",\n'
            '      "candidate_id": "path:line:index",\n'
            '      "routeable": true,\n'
            '      "reason": "为什么这个专家应该看这个 hunk",\n'
            '      "confidence": 0.91\n'
            "    }\n"
            "  ],\n"
            '  "skipped_experts": [\n'
            '    {"expert_id": "ddd_specification", "reason": "未命中领域建模变化"}\n'
            "  ]\n"
            "}"
        )

    def _build_expert_selection_user_prompt(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        requested_expert_ids: list[str],
    ) -> str:
        business_changed_files = self._candidate_changed_files(subject, "")
        expert_sections = []
        for expert in experts:
            expert_sections.append(
                "\n".join(
                    [
                        f"- expert_id: {expert.expert_id}",
                        f"  名称: {expert.name_zh}",
                        f"  角色: {expert.role}",
                        f"  职责重点: {' / '.join(expert.focus_areas) or expert.role}",
                        f"  触发线索: {' / '.join(expert.activation_hints) or '按变更语义判断'}",
                        f"  必查项: {' / '.join(expert.required_checks) or '无'}",
                        f"  越界边界: {' / '.join(expert.out_of_scope) or '无'}",
                    ]
                )
            )
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"MR 链接: {subject.mr_url}\n"
            f"源分支: {subject.source_ref}\n"
            f"目标分支: {subject.target_ref}\n"
            f"全部变更文件: {json.dumps(list(subject.changed_files), ensure_ascii=False)}\n"
            f"业务变更文件: {json.dumps(business_changed_files, ensure_ascii=False)}\n"
            f"用户原始选择: {json.dumps(requested_expert_ids, ensure_ascii=False)}\n"
            f"完整 diff:\n{subject.unified_diff[:12000]}\n\n"
            f"可用专家画像:\n{chr(10).join(expert_sections)}\n\n"
            "请输出 JSON，格式为：\n"
            "{\n"
            '  "selected_experts": [\n'
            '    {"expert_id": "correctness_business", "reason": "跨文件字段契约和业务语义变化明显", "confidence": 0.93}\n'
            "  ],\n"
            '  "skipped_experts": [\n'
            '    {"expert_id": "security_compliance", "reason": "当前 diff 未出现认证、权限、密钥或输入校验相关信号"}\n'
            "  ]\n"
            "}"
        )

    def _parse_json_payload(self, text: str) -> dict[str, object]:
        content = str(text or "").strip()
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif content.startswith("```"):
            content = content.split("```", 1)[1].rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _parse_routing_plan(self, text: str) -> dict[str, object]:
        return self._parse_json_payload(text)

    def _merge_expert_selection(
        self,
        *,
        experts: list[ExpertProfile],
        requested_expert_ids: list[str],
        llm_payload: dict[str, object],
        fallback_ids: list[str],
    ) -> dict[str, object]:
        experts_by_id = {expert.expert_id: expert for expert in experts}
        selected_entries: list[dict[str, object]] = []
        selected_ids: list[str] = []
        for item in list(llm_payload.get("selected_experts", []) or []):
            if not isinstance(item, dict):
                continue
            expert_id = str(item.get("expert_id") or "").strip()
            if not expert_id or expert_id not in experts_by_id or expert_id in selected_ids:
                continue
            selected_ids.append(expert_id)
            selected_entries.append(
                {
                    "expert_id": expert_id,
                    "expert_name": experts_by_id[expert_id].name_zh,
                    "reason": str(item.get("reason") or "").strip(),
                    "confidence": float(item.get("confidence") or 0.0),
                    "source": "llm_selected",
                }
            )
        if not selected_ids:
            selected_ids = list(fallback_ids)
            selected_entries = [
                {
                    "expert_id": expert_id,
                    "expert_name": experts_by_id[expert_id].name_zh,
                    "reason": "LLM 未返回有效专家集合，已使用兜底集合",
                    "confidence": 0.5,
                    "source": "fallback_selected",
                }
                for expert_id in selected_ids
                if expert_id in experts_by_id
            ]
        skipped_entries: list[dict[str, object]] = []
        explicit_skipped_ids: set[str] = set()
        for item in list(llm_payload.get("skipped_experts", []) or []):
            if not isinstance(item, dict):
                continue
            expert_id = str(item.get("expert_id") or "").strip()
            if not expert_id or expert_id not in experts_by_id or expert_id in explicit_skipped_ids:
                continue
            explicit_skipped_ids.add(expert_id)
            skipped_entries.append(
                {
                    "expert_id": expert_id,
                    "expert_name": experts_by_id[expert_id].name_zh,
                    "reason": str(item.get("reason") or "").strip() or "大模型判定当前 MR 与该专家职责不匹配",
                }
            )
        for expert in experts:
            if expert.expert_id in selected_ids or expert.expert_id in explicit_skipped_ids:
                continue
            skipped_entries.append(
                {
                    "expert_id": expert.expert_id,
                    "expert_name": expert.name_zh,
                    "reason": "大模型未将该专家纳入本次 MR 的参与集合",
                }
            )
        return {
            "requested_expert_ids": requested_expert_ids,
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": selected_ids,
            "selected_experts": selected_entries,
            "skipped_experts": skipped_entries,
        }

    def _merge_routing_plan(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        baseline_routes: dict[str, dict[str, object]],
        candidate_hunks: list[dict[str, object]],
        llm_plan: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        candidate_index = {str(item["candidate_id"]): item for item in candidate_hunks}
        skipped_by_id = {
            str(item.get("expert_id") or ""): str(item.get("reason") or "").strip()
            for item in list(llm_plan.get("skipped_experts", []) or [])
            if isinstance(item, dict)
        }
        route_entries = {
            str(item.get("expert_id") or ""): item
            for item in list(llm_plan.get("expert_routes", []) or [])
            if isinstance(item, dict)
        }
        merged: dict[str, dict[str, object]] = {}
        for expert in experts:
            baseline = dict(baseline_routes.get(expert.expert_id) or {})
            entry = route_entries.get(expert.expert_id)
            if not entry:
                if expert.expert_id in skipped_by_id:
                    baseline["routeable"] = False
                    baseline["skip_reason"] = skipped_by_id[expert.expert_id]
                    baseline["routing_source"] = "llm_skip"
                    baseline["confidence"] = 0.35
                merged[expert.expert_id] = baseline
                continue
            candidate = candidate_index.get(str(entry.get("candidate_id") or ""))
            if not candidate:
                merged[expert.expert_id] = baseline
                continue
            routeable = bool(entry.get("routeable", True))
            changed_lines = self._normalize_changed_lines(candidate.get("line_start"))
            merged[expert.expert_id] = {
                **baseline,
                "expert_id": expert.expert_id,
                "file_path": str(candidate.get("file_path") or baseline.get("file_path") or ""),
                "line_start": int(candidate.get("line_start") or baseline.get("line_start") or 1),
                "target_hunk": {
                    "file_path": candidate.get("file_path"),
                    "hunk_header": candidate.get("hunk_header"),
                    "start_line": candidate.get("line_start"),
                    "end_line": candidate.get("line_start"),
                    "changed_lines": changed_lines,
                    "excerpt": candidate.get("excerpt"),
                },
                "repo_hits": dict(candidate.get("repo_hits") or {}),
                "routing_reason": str(entry.get("reason") or baseline.get("routing_reason") or ""),
                "confidence": float(entry.get("confidence") or baseline.get("confidence") or 0.0),
                "routeable": routeable,
                "skip_reason": "" if routeable else str(skipped_by_id.get(expert.expert_id) or entry.get("reason") or baseline.get("skip_reason") or ""),
                "routing_source": "llm",
            }
        return merged

    def _normalize_changed_lines(self, line_start: object) -> list[int]:
        try:
            value = int(line_start or 1)
        except (TypeError, ValueError):
            value = 1
        return [value]

    def _match_candidate_id(
        self,
        candidate_hunks: list[dict[str, object]],
        route: dict[str, object] | None,
    ) -> str:
        route = route or {}
        file_path = str(route.get("file_path") or "")
        line_start = int(route.get("line_start") or 0)
        for item in candidate_hunks:
            if str(item.get("file_path") or "") == file_path and int(item.get("line_start") or 0) == line_start:
                return str(item.get("candidate_id") or "")
        return ""

    def _format_repo_matches(self, repo_hits: dict[str, object]) -> str:
        matches = list(repo_hits.get("matches", []) or [])
        fragments: list[str] = []
        for item in matches[:4]:
            path = str(item.get("path") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            if path:
                fragments.append(f"{path} {snippet}".strip())
        for symbol_context in list(repo_hits.get("symbol_contexts", []) or [])[:2]:
            if not isinstance(symbol_context, dict):
                continue
            symbol = str(symbol_context.get("symbol") or "").strip()
            definitions = list(symbol_context.get("definitions", []) or [])
            references = list(symbol_context.get("references", []) or [])
            if symbol:
                fragments.append(f"symbol:{symbol} defs={len(definitions)} refs={len(references)}")
        return "\n".join(fragments)
