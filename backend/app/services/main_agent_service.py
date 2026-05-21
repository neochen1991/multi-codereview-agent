from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.cross_file_impact import build_cross_file_impact_hints
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.expert_capability_service import ExpertCapabilityService
from app.services.code_observation_extractor import CodeObservationExtractor
from app.services.llm_chat_service import LLMChatService, LLMTextResult
from app.services.main_agent_prompting import MainAgentPromptingMixin
from app.services.repo_review_instruction_service import RepoReviewInstructionService
from app.services.repository_context_service import RepositoryContextService
from app.services.repository_config_resolver import RepositoryConfigResolver
from app.services.sast_prescan_service import SastPreScanService


class MainAgentService(MainAgentPromptingMixin):
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
        self._java_quality_signal_extractor = CodeObservationExtractor()
        self._repo_review_instruction_service = RepoReviewInstructionService()
        self._sast_prescan_service = SastPreScanService()
        self._repository_resolver = RepositoryConfigResolver()
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
        repository_service = self._build_repository_service(runtime_settings, subject)
        target_focus = route_hint or self._build_rule_route(subject, expert, repository_service)
        file_path = str(target_focus.get("file_path") or self._pick_file_path(subject, expert))
        line_start = (
            int(target_focus.get("line_start") or self._pick_line_start(subject, expert.expert_id, file_path))
            if file_path
            else 0
        )
        target_hunk = dict(target_focus.get("target_hunk") or {})
        target_hunks = [dict(item) for item in list(target_focus.get("target_hunks") or []) if isinstance(item, dict)]
        if not target_hunk and file_path:
            best_hunk = self._diff_excerpt_service.find_best_hunk(
                subject.unified_diff,
                file_path,
                line_start or 1,
            )
            if best_hunk:
                target_hunk = dict(best_hunk)
        if not target_hunks and target_hunk:
            target_hunks = [dict(target_hunk)]
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
            runtime_settings,
            file_path,
            line_start,
            related_files,
            list(subject.changed_files or []),
            dict(target_focus.get("repo_hits") or {}),
            str(target_hunk.get("excerpt") or ""),
        )
        if route_hint is not None:
            routeable = bool(target_focus.get("routeable", True))
            skip_reason = "" if routeable else str(target_focus.get("skip_reason") or "")
        else:
            routeable, skip_reason = self._should_route_expert(
                subject,
                expert,
                {**target_focus, "target_hunk": target_hunk},
                file_path,
            )
        summary = self._build_command_fallback(
            subject,
            expert,
            file_path,
            line_start,
            target_hunk=target_hunk,
            target_hunks=target_hunks,
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
            "target_hunks": target_hunks,
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
        repository_service = self._build_repository_service(runtime_settings, subject)
        baseline_routes = {
            expert.expert_id: self._build_rule_route(subject, expert, repository_service)
            for expert in experts
        }
        baseline_routes = self._preserve_selected_expert_routes(experts, baseline_routes)

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
                runtime_settings=runtime_settings,
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
        merged = self._preserve_selected_expert_routes(experts, merged)
        for route in merged.values():
            route["routing_llm"] = self._llm_metadata(result)
        return merged

    def build_candidate_hunks(
        self,
        subject: ReviewSubject,
        runtime_settings: RuntimeSettings,
    ) -> list[dict[str, object]]:
        """返回当前审核任务中需要覆盖的全部候选代码 hunk。"""

        repository_service = self._build_repository_service(runtime_settings, subject)
        return self._build_candidate_hunks(subject, repository_service)

    def clear_runtime_caches(self) -> None:
        """清理主 Agent 本地缓存，避免常驻进程保留历史上下文。"""

        self._repo_context_cache.clear()

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
                runtime_settings=runtime_settings,
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
            subject=subject,
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
        partial_failure_count: int = 0,
        finding_count: int = 0,
        filtered_finding_count: int = 0,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
    ) -> tuple[str, dict[str, object]]:
        """让主 Agent 在 issue 收敛后输出控制台播报式总结。"""
        blocker_count = len([issue for issue in issues if issue.severity in {"blocker", "critical"}])
        pending_count = len([issue for issue in issues if issue.needs_human and issue.status != "resolved"])
        finding_note = ""
        if not issues and finding_count > 0:
            finding_note = (
                f"本轮形成 {finding_count} 条 findings，其中 {filtered_finding_count} 条未升级为 issues；"
                "这表示候选风险被保留或过滤，不等同于“无问题”。"
            )
        if partial_failure_count > 0:
            fallback_text = (
                f"主Agent收敛完成：本轮共有 {len(issues)} 个议题，blocker/critical {blocker_count} 个，"
                f"待人工裁决 {pending_count} 个。另有 {partial_failure_count} 个专家任务执行失败，"
                "当前结果应视为部分完成，请优先重试失败专家后再做最终放行判断。"
            )
        elif finding_note:
            fallback_text = f"主Agent收敛完成：{finding_note} 请在报告 findings 与过滤原因中继续核验。"
        else:
            fallback_text = (
                f"主Agent收敛完成：本次共形成 {len(issues)} 个议题，其中 blocker/critical {blocker_count} 个，"
                f"待人工裁决 {pending_count} 个，审核状态 {review.status}，下一步请优先处理高风险结论。"
            )
        resolution = self._llm.resolve_main_agent(runtime_settings)
        user_prompt = (
            f"审核状态: {review.status}\n"
            f"审核阶段: {review.phase}\n"
            f"议题总数: {len(issues)}\n"
            f"专家 finding 总数: {finding_count}\n"
            f"未升级为 issue 的 finding 数: {filtered_finding_count}\n"
            f"高风险议题数: {blocker_count}\n"
            f"待人工裁决数: {pending_count}\n"
            f"专家执行失败数: {partial_failure_count}\n"
            f"候选风险说明: {finding_note or '无'}\n"
            "如果 issue 为 0 但 finding 或过滤候选大于 0，必须明确说明“不是无问题”，而是候选未升级或需继续核验。\n"
            f"请输出一段中文总结，风格像主Agent对控制台的收敛播报。"
        )
        try:
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
        except RuntimeError as error:
            return fallback_text, {
                "mode": "fallback",
                "provider": getattr(resolution, "provider", ""),
                "model": getattr(resolution, "model", ""),
                "error": str(error),
            }
        return result.text.strip(), self._llm_metadata(result)

    def _allow_fallback(self, runtime_settings: RuntimeSettings) -> bool:
        return bool(runtime_settings.allow_llm_fallback)

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
        target_hunks: list[dict[str, object]] | None = None,
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
        hunk_count = len([item for item in list(target_hunks or []) if isinstance(item, dict)]) or (1 if target_hunk else 0)
        return (
            f"**派工指令**\n\n"
            f"**目标专家：** {expert.expert_id} / {expert.name_zh}\n\n"
            f"**审查对象：** {subject.title or subject.mr_url or subject.source_ref}\n\n"
            f"**定向任务：** 请聚焦文件 `{file_path}` 第 **{line_start} 行** 附近的变更，"
            f"重点从“{focus}”视角审查。\n\n"
            f"**目标 hunk：** {hunk_header}\n"
            f"**覆盖范围：** 当前文件共 {hunk_count or 1} 个变更 hunk，需要联合审查。\n"
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
        if expert_id in {"ddd_specification", "ddd_architecture"}:
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

    def _preserve_selected_expert_routes(
        self,
        experts: list[ExpertProfile],
        routes: dict[str, dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        preserved: dict[str, dict[str, object]] = {}
        for expert in experts:
            route = dict(routes.get(expert.expert_id) or {})
            if not route:
                preserved[expert.expert_id] = route
                continue
            file_path = str(route.get("file_path") or "").strip()
            if not file_path:
                preserved[expert.expert_id] = route
                continue
            if bool(route.get("routeable", True)):
                preserved[expert.expert_id] = route
                continue

            previous_reason = str(route.get("skip_reason") or "").strip()
            routing_reason = str(route.get("routing_reason") or "").strip()
            override_reason = "主Agent已选中该专家，本轮按保守执行策略继续审查，避免执行层二次静默跳过。"
            route["routeable"] = True
            route["skip_reason"] = ""
            route["confidence"] = max(float(route.get("confidence") or 0.0), 0.31)
            route["routing_source"] = "selected_override"
            combined_reason = " ".join(part for part in [routing_reason, override_reason] if part).strip()
            route["routing_reason"] = combined_reason or override_reason
            if previous_reason:
                route["routing_override_reason"] = previous_reason
            preserved[expert.expert_id] = route
        return preserved

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
            preferred_line = 24
        elif expert_id == "performance_reliability":
            preferred_line = 57
        elif expert_id == "database_analysis":
            preferred_line = 36
        elif expert_id == "redis_analysis":
            preferred_line = 28
        elif expert_id == "mq_analysis":
            preferred_line = 30
        elif expert_id in {"ddd_specification", "ddd_architecture"}:
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

    def _has_security_signal(self, subject: ReviewSubject, file_path: str, target_hunk: dict[str, object]) -> bool:
        lowered = "\n".join(
            [
                str(file_path or "").lower(),
                str(target_hunk.get("excerpt") or "").lower(),
                self._strip_non_diff_signal_lines(str(subject.unified_diff or "")).lower(),
            ]
        )
        tokens = [
            "auth",
            "security",
            "permission",
            "token",
            "secret",
            "@valid",
            "validation",
            "bindingresult",
            "requestbody",
            "requestparam",
            "input",
            "sanitize",
            "csrf",
            "bean validation",
        ]
        return any(token in lowered for token in tokens)

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
                "@valid",
                "validation",
                "bindingresult",
                "requestbody",
                "requestparam",
                "bean validation",
                "sanitize",
                "csrf",
            ]
        ):
            return False, "当前变更未命中安全相关线索"
        if expert_id == "frontend_accessibility" and "frontend" not in lowered:
            return False, "当前变更不属于前端可访问性审查范围"
        if expert_id in {"ddd_specification", "ddd_architecture", "architecture_design", "maintainability_code_health", "security_compliance"} and import_only:
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
        metadata = {
            "llm_call_id": result.call_id,
            "provider": result.provider,
            "model": result.model,
            "base_url": result.base_url,
            "api_key_env": result.api_key_env,
            "mode": result.mode,
            "error": result.error,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
        }
        trace = {
            "system_prompt_snapshot_full": str(getattr(result, "system_prompt_snapshot_full", "") or ""),
            "prompt_snapshot_full": str(getattr(result, "prompt_snapshot_full", "") or ""),
            "model_raw_response_full": str(getattr(result, "model_raw_response_full", "") or result.text or ""),
        }
        if any(trace.values()):
            metadata["llm_trace"] = trace
        return metadata

    def _build_repository_context(
        self,
        service: RepositoryContextService,
        runtime_settings: RuntimeSettings,
        file_path: str,
        line_start: int,
        related_files: list[str],
        changed_files: list[str],
        repo_hits: dict[str, object] | None = None,
        target_hunk_excerpt: str = "",
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
            "changed_files": [str(item).strip() for item in list(changed_files or []) if str(item).strip()],
            "target_hunk_excerpt": str(target_hunk_excerpt or "").strip(),
            "repo_review_instructions": self._repo_review_instruction_service.load_for_file(
                service.local_path,
                file_path,
            ),
            "sast_prescan": self._sast_prescan_service.scan_file(
                service.local_path,
                file_path,
                enabled=runtime_settings.enable_sast_prescan,
            ),
            "cross_file_impact_hints": build_cross_file_impact_hints(
                file_path=file_path,
                related_files=related_files,
                repo_hits=repo_hits,
                changed_files=changed_files,
                target_hunk_excerpt=target_hunk_excerpt,
            ),
        }

    def _candidate_changed_files(self, subject: ReviewSubject, expert_id: str) -> list[str]:
        changed_files = [item for item in subject.changed_files if item]
        if expert_id == "test_verification":
            test_files = [item for item in changed_files if self._is_test_like_path(item)]
            return test_files or changed_files
        business_files = [item for item in changed_files if not self._is_test_like_path(item)]
        return business_files or changed_files

    def _is_test_like_path(self, path: str) -> bool:
        normalized = Path(str(path or "").replace("\\", "/"))
        parts = normalized.parts
        if any(part.lower() in self.TEST_PATH_MARKERS for part in parts):
            return True
        name = normalized.name
        stem = normalized.stem
        lower_name = name.lower()
        lower_stem = stem.lower()
        if any(token in lower_name for token in [".test.", ".tests.", ".spec.", ".specs.", ".it."]):
            return True
        if lower_stem in {"test", "tests", "spec", "specs"}:
            return True
        if any(lower_stem.endswith(suffix) for suffix in ("_test", "_tests", "_spec", "_specs", "-test", "-tests", "-spec", "-specs")):
            return True
        return bool(re.search(r"(Test|Tests|Spec|Specs|IT|ITCase)$", stem))

    def _build_repository_service(
        self,
        runtime_settings: RuntimeSettings,
        subject: ReviewSubject | None = None,
    ) -> RepositoryContextService:
        return self._repository_resolver.build_context_service(runtime_settings, subject)

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
                observation_payload = self._java_quality_signal_extractor.extract(
                    file_path=file_path,
                    target_hunk=hunk,
                    full_diff=str(hunk.get("excerpt") or ""),
                )
                hunk_risk_signals = [
                    str(item).strip()
                    for item in list(observation_payload.get("signals") or [])
                    if str(item).strip()
                ]
                candidates.append(
                    {
                        "candidate_id": f"{file_path}:{line_start}:{index}",
                        "file_path": file_path,
                        "line_start": line_start,
                        "start_line": int(hunk.get("start_line") or line_start),
                        "end_line": int(hunk.get("end_line") or line_start),
                        "changed_lines": changed_lines or [line_start],
                        "hunk_header": str(hunk.get("hunk_header") or ""),
                        "excerpt": str(hunk.get("excerpt") or ""),
                        "import_only": bool(item["import_only"]),
                        "format_only": bool(item["format_only"]),
                        "risk_signals": hunk_risk_signals,
                        "repo_hits": repo_hits,
                        "cross_file_impact_hints": build_cross_file_impact_hints(
                            file_path=file_path,
                            related_files=[],
                            repo_hits=repo_hits,
                            changed_files=list(subject.changed_files or []),
                        ),
                    }
                )
        return candidates













