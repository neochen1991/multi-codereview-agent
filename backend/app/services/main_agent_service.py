from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.expert_capability_service import ExpertCapabilityService
from app.services.java_quality_signal_extractor import JavaQualitySignalExtractor
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
        self._java_quality_signal_extractor = JavaQualitySignalExtractor()
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
            file_path,
            line_start,
            related_files,
            dict(target_focus.get("repo_hits") or {}),
        )
        batch_items_payload: list[dict[str, object]] = []
        for item in [dict(value) for value in list(target_focus.get("batch_items") or []) if isinstance(value, dict)]:
            item_file_path = str(item.get("file_path") or "").strip()
            if not item_file_path:
                continue
            item_line_start = int(item.get("line_start") or self._pick_line_start(subject, expert.expert_id, item_file_path))
            item_target_hunk = dict(item.get("target_hunk") or {})
            item_target_hunks = [dict(value) for value in list(item.get("target_hunks") or []) if isinstance(value, dict)]
            item_repo_hits = dict(item.get("repo_hits") or {})
            item_related_files = self._build_expert_related_files(
                subject,
                expert,
                item_file_path,
                change_chain["related_files"],
            )
            item_repo_context = self._build_repository_context(
                repository_service,
                item_file_path,
                item_line_start,
                item_related_files,
                item_repo_hits,
            )
            batch_items_payload.append(
                {
                    "file_path": item_file_path,
                    "line_start": item_line_start,
                    "target_hunk": item_target_hunk,
                    "target_hunks": item_target_hunks,
                    "related_files": item_related_files,
                    "repository_context": item_repo_context,
                }
            )
        if route_hint is not None:
            routeable = bool(target_focus.get("routeable", True))
            skip_reason = "" if routeable else str(target_focus.get("skip_reason") or "")
        else:
            routeable, skip_reason = self._should_route_expert(subject, expert, target_focus, file_path)
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
            "batch_items": batch_items_payload,
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
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
    ) -> tuple[str, dict[str, object]]:
        """让主 Agent 在 issue 收敛后输出控制台播报式总结。"""
        blocker_count = len([issue for issue in issues if issue.severity in {"blocker", "critical"}])
        pending_count = len([issue for issue in issues if issue.needs_human and issue.status != "resolved"])
        if partial_failure_count > 0:
            fallback_text = (
                f"主Agent收敛完成：本轮共有 {len(issues)} 个议题，blocker/critical {blocker_count} 个，"
                f"待人工裁决 {pending_count} 个。另有 {partial_failure_count} 个专家任务执行失败，"
                "当前结果应视为部分完成，请优先重试失败专家后再做最终放行判断。"
            )
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
            f"高风险议题数: {blocker_count}\n"
            f"待人工裁决数: {pending_count}\n"
            f"专家执行失败数: {partial_failure_count}\n"
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

    def _build_repository_service(self, runtime_settings: RuntimeSettings, subject: ReviewSubject) -> RepositoryContextService:
        return RepositoryContextService.from_review_context(
            clone_url=runtime_settings.code_repo_clone_url,
            local_path=runtime_settings.code_repo_local_path,
            default_branch=runtime_settings.code_repo_default_branch or runtime_settings.default_target_branch,
            access_token=runtime_settings.code_repo_access_token,
            auto_sync=runtime_settings.code_repo_auto_sync,
            subject=subject,
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

    def _infer_code_language(self, file_path: str) -> str:
        lowered = str(file_path or "").lower()
        if lowered.endswith(".tsx"):
            return "tsx"
        if lowered.endswith(".ts"):
            return "typescript"
        if lowered.endswith(".jsx"):
            return "jsx"
        if lowered.endswith(".js"):
            return "javascript"
        if lowered.endswith(".java"):
            return "java"
        return "text"

    def _build_language_general_guidance(self, language: str) -> str:
        normalized = str(language or "").strip().lower()
        if normalized == "java":
            return (
                "- 参考 Java / Spring 通用代码规范：命名清晰，职责单一，校验、事务、持久化、远程调用不要无边界混合，避免临时变量名、弱语义命名和魔法值直接写进业务逻辑。\n"
                "- 关注输入校验、空值与异常处理、日志脱敏、权限/租户隔离，以及 @Transactional 内的副作用。\n"
                "- 检查 Repository / JPA / MyBatis 查询是否存在无分页、全表扫描、N+1、批量逐条写等常见质量风险。\n"
                "- 检查条件分支、阈值、状态码、字符串标识是否以魔法值形式散落在代码中，是否应提取为常量、枚举或具名配置。"
            )
        if normalized in {"javascript", "jsx", "typescript", "tsx"}:
            return (
                "- 参考 JavaScript / TypeScript 通用代码规范：命名清晰，副作用显式，异步错误必须处理，避免隐式 any 和不透明的数据流。\n"
                "- 关注输入校验、鉴权边界、敏感信息暴露、Promise/await 错误传播、竞态和资源泄露。\n"
                "- 检查数据库/HTTP/缓存调用是否存在未分页查询、无边界重试、串行批处理或阻塞主路径的问题。"
            )
        return ""

    def _build_language_general_guidance_summary(self, file_paths: list[str]) -> str:
        sections: list[str] = []
        seen_languages: set[str] = set()
        for path in file_paths:
            language = self._infer_code_language(path)
            if language in seen_languages:
                continue
            seen_languages.add(language)
            guidance = self._build_language_general_guidance(language)
            if not guidance:
                continue
            sections.append(f"# {language}\n{guidance}")
        return "\n\n".join(sections) if sections else "当前变更文件未命中已配置的语言通用规范提示。"

    def _build_routing_user_prompt(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        candidate_hunks: list[dict[str, object]],
        runtime_settings: RuntimeSettings,
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
        primary_file_path = str(candidate_hunks[0]["file_path"]) if candidate_hunks else str(business_changed_files[0]) if business_changed_files else ""
        target_file_full_diff = self._build_file_diff_context(subject, primary_file_path, max_lines=100)
        related_diff_summary = self._build_related_diff_summary(subject, primary_file_path, max_files=3, max_lines_per_file=16)
        source_context_summary = self._build_main_agent_source_context_summary(
            subject,
            runtime_settings,
            primary_file_path=primary_file_path,
            related_file_paths=business_changed_files,
            primary_radius=10,
            primary_max_lines=16,
            related_radius=6,
            related_max_files=2,
            related_max_lines=8,
        )
        language_general_guidance = self._build_language_general_guidance_summary(
            [str(item.get("file_path") or "") for item in candidate_hunks] + business_changed_files
        )
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"源分支: {subject.source_ref}\n"
            f"目标分支: {subject.target_ref}\n"
            f"全部变更文件: {json.dumps(list(subject.changed_files), ensure_ascii=False)}\n"
            f"业务变更文件: {json.dumps(business_changed_files, ensure_ascii=False)}\n"
            f"目标文件完整 diff:\n{target_file_full_diff}\n\n"
            f"其他变更文件摘要:\n{related_diff_summary}\n\n"
            f"变更源码与关联上下文:\n{source_context_summary}\n\n"
            f"语言通用规范提示:\n{language_general_guidance}\n\n"
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
        runtime_settings: RuntimeSettings,
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
        primary_file_path = str(business_changed_files[0]) if business_changed_files else str(subject.changed_files[0]) if subject.changed_files else ""
        target_file_full_diff = self._build_file_diff_context(subject, primary_file_path, max_lines=80)
        related_diff_summary = self._build_related_diff_summary(subject, primary_file_path, max_files=2, max_lines_per_file=12)
        java_quality = self._collect_java_quality_signals(subject)
        java_quality_summary = self._build_java_quality_signal_summary(java_quality)
        language_general_guidance = self._build_language_general_guidance_summary(
            business_changed_files or [str(item) for item in list(subject.changed_files or [])]
        )
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"MR 链接: {subject.mr_url}\n"
            f"源分支: {subject.source_ref}\n"
            f"目标分支: {subject.target_ref}\n"
            f"全部变更文件: {json.dumps(list(subject.changed_files), ensure_ascii=False)}\n"
            f"业务变更文件: {json.dumps(business_changed_files, ensure_ascii=False)}\n"
            f"用户原始选择: {json.dumps(requested_expert_ids, ensure_ascii=False)}\n"
            f"业务变更文件完整 diff:\n{target_file_full_diff}\n\n"
            f"其他变更文件摘要:\n{related_diff_summary}\n\n"
            f"Java 质量信号摘要:\n{java_quality_summary}\n\n"
            f"语言通用规范提示:\n{language_general_guidance}\n\n"
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

    def _build_java_quality_signal_summary(self, java_quality: dict[str, list[str]]) -> str:
        signals = [str(item).strip() for item in list(java_quality.get("signals") or []) if str(item).strip()]
        matched_terms = [str(item).strip() for item in list(java_quality.get("matched_terms") or []) if str(item).strip()]
        if not signals and not matched_terms:
            return "当前变更未提取到额外的 Java 质量信号。"
        lines: list[str] = []
        if signals:
            lines.append(f"- signals: {', '.join(signals)}")
        if matched_terms:
            lines.append(f"- matched_terms: {', '.join(matched_terms[:12])}")
        return "\n".join(lines)

    def _build_file_diff_context(self, subject: ReviewSubject, file_path: str, *, max_lines: int = 160) -> str:
        if not file_path:
            return "未定位到主要变更文件。"
        full_diff = self._diff_excerpt_service.extract_file_diff(subject.unified_diff, file_path)
        if not full_diff:
            return f"未从 unified diff 中提取到 {file_path} 的文件级变更。"
        lines = full_diff.splitlines()
        if len(lines) <= max_lines:
            return full_diff
        return "\n".join(lines[:max_lines]) + f"\n... [目标文件完整 diff 过长，已截断展示前 {max_lines} 行]"

    def _build_related_diff_summary(
        self,
        subject: ReviewSubject,
        primary_file_path: str,
        *,
        max_files: int = 4,
        max_lines_per_file: int = 24,
    ) -> str:
        related_paths = [
            str(path).strip()
            for path in list(subject.changed_files or [])
            if str(path).strip() and str(path).strip() != primary_file_path
        ]
        if not related_paths:
            return "除主要变更文件外无其他变更文件。"
        sections: list[str] = []
        for path in related_paths[:max_files]:
            full_diff = self._diff_excerpt_service.extract_file_diff(subject.unified_diff, path)
            if not full_diff:
                sections.append(f"# {path}\n未提取到该文件 diff。")
                continue
            preview_lines = full_diff.splitlines()
            display_lines = preview_lines[:max_lines_per_file]
            suffix = "\n... [摘要已截断]" if len(preview_lines) > max_lines_per_file else ""
            sections.append(f"# {path}\n" + "\n".join(display_lines) + suffix)
        remaining = len(related_paths) - min(len(related_paths), max_files)
        if remaining > 0:
            sections.append(f"... 其余 {remaining} 个变更文件未展开，请结合 changed_files 判断全局影响。")
        return "\n\n".join(sections)

    def _build_main_agent_source_context_summary(
        self,
        subject: ReviewSubject,
        runtime_settings: RuntimeSettings,
        *,
        primary_file_path: str,
        related_file_paths: list[str],
        primary_radius: int = 16,
        primary_max_lines: int = 24,
        related_radius: int = 10,
        related_max_files: int = 3,
        related_max_lines: int = 16,
    ) -> str:
        service = self._build_repository_service(runtime_settings, subject)
        if not service.is_ready():
            return "代码仓上下文未配置或本地仓不可用；当前只提供真实 diff，未补充目标分支源码。"
        sections: list[str] = []
        if primary_file_path:
            primary_line = self._pick_line_start(subject, "", primary_file_path)
            primary_context = service.load_file_context(primary_file_path, max(1, primary_line), radius=primary_radius)
            primary_snippet = str((primary_context or {}).get("snippet") or "").strip()
            if primary_snippet:
                sections.append(f"# 目标文件源码\n{primary_file_path}")
                sections.extend(primary_snippet.splitlines()[:primary_max_lines])
        for path in [
            item
            for item in related_file_paths
            if str(item).strip() and str(item).strip() != primary_file_path
        ][:related_max_files]:
            line_start = self._pick_line_start(subject, "", path)
            context = service.load_file_context(path, max(1, line_start), radius=related_radius)
            snippet = str((context or {}).get("snippet") or "").strip()
            if not snippet:
                continue
            if sections:
                sections.append("")
            sections.append(f"# 关联源码\n{path}")
            sections.extend(snippet.splitlines()[:related_max_lines])
        return "\n".join(sections) if sections else "未从目标分支源码仓加载到可用源码片段。"

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
        subject: ReviewSubject,
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
        if "security_compliance" in requested_expert_ids and "security_compliance" not in selected_ids:
            security_expert = experts_by_id.get("security_compliance")
            if security_expert is not None:
                file_path = self._pick_file_path(subject, security_expert)
                target_focus = self._build_rule_route(subject, security_expert, self._build_repository_service(RuntimeSettings(), subject))
                if self._has_security_signal(subject, file_path, dict(target_focus.get("target_hunk") or {})):
                    selected_ids.append("security_compliance")
                    selected_entries.append(
                        {
                            "expert_id": "security_compliance",
                            "expert_name": security_expert.name_zh,
                            "reason": "命中 Java 入口校验/输入验证安全线索，系统补入安全与合规专家复核。",
                            "confidence": 0.78,
                            "source": "heuristic_selected",
                        }
                    )
                    skipped_entries = [item for item in skipped_entries if str(item.get("expert_id") or "") != "security_compliance"]
        java_quality = self._collect_java_quality_signals(subject)
        selected_ids, selected_entries, skipped_entries = self._apply_java_signal_expert_retention(
            requested_expert_ids=requested_expert_ids,
            experts_by_id=experts_by_id,
            selected_ids=selected_ids,
            selected_entries=selected_entries,
            skipped_entries=skipped_entries,
            java_quality_signals=java_quality["signals"],
        )
        return {
            "requested_expert_ids": requested_expert_ids,
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": selected_ids,
            "selected_experts": selected_entries,
            "skipped_experts": skipped_entries,
        }

    def _collect_java_quality_signals(self, subject: ReviewSubject) -> dict[str, list[str]]:
        signals: list[str] = []
        matched_terms: list[str] = []
        for file_path in list(subject.changed_files or []):
            if Path(str(file_path or "")).suffix.lower() != ".java":
                continue
            file_diff = self._diff_excerpt_service.extract_file_diff(str(subject.unified_diff or ""), str(file_path))
            if not file_diff.strip():
                continue
            extracted = self._java_quality_signal_extractor.extract(
                file_path=str(file_path),
                target_hunk={"excerpt": file_diff},
                full_diff=file_diff,
            )
            for signal in list(extracted.get("signals") or []):
                normalized = str(signal).strip()
                if normalized and normalized not in signals:
                    signals.append(normalized)
            for term in list(extracted.get("matched_terms") or []):
                normalized = str(term).strip()
                if normalized and normalized not in matched_terms:
                    matched_terms.append(normalized)
        return {"signals": signals, "matched_terms": matched_terms}

    def _apply_java_signal_expert_retention(
        self,
        *,
        requested_expert_ids: list[str],
        experts_by_id: dict[str, ExpertProfile],
        selected_ids: list[str],
        selected_entries: list[dict[str, object]],
        skipped_entries: list[dict[str, object]],
        java_quality_signals: list[str],
    ) -> tuple[list[str], list[dict[str, object]], list[dict[str, object]]]:
        signal_set = {str(item).strip() for item in java_quality_signals if str(item).strip()}
        if not signal_set:
            return selected_ids, selected_entries, skipped_entries

        def _add_if_requested(expert_id: str, reason: str, confidence: float) -> None:
            if expert_id not in requested_expert_ids or expert_id in selected_ids:
                return
            expert = experts_by_id.get(expert_id)
            if expert is None:
                return
            selected_ids.append(expert_id)
            selected_entries.append(
                {
                    "expert_id": expert_id,
                    "expert_name": expert.name_zh,
                    "reason": reason,
                    "confidence": confidence,
                    "source": "heuristic_selected",
                }
            )

        if {"factory_bypass", "event_ordering_risk"} & signal_set:
            _add_if_requested(
                "architecture_design",
                "检测到聚合工厂绕过或事件发布顺序风险，系统补入架构与设计专家复核结构边界与事务顺序。",
                0.81,
            )

        if {"query_semantics_weakened", "exception_swallowed"} & signal_set:
            _add_if_requested(
                "security_compliance",
                "检测到查询语义放宽或异常处理退化，系统补入安全与合规专家复核数据访问面与错误处理边界。",
                0.76,
            )

        if {"naming_convention_violation", "magic_value_literal", "exception_swallowed"} & signal_set:
            _add_if_requested(
                "maintainability_code_health",
                "检测到命名规范、魔法值或异常处理质量退化，系统补入可维护性与代码健康专家复核语言层质量问题。",
                0.72,
            )

        selected_set = set(selected_ids)
        filtered_skipped = [
            item for item in skipped_entries if str(item.get("expert_id") or "").strip() not in selected_set
        ]
        return selected_ids, selected_entries, filtered_skipped

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
                fragments.append(f"{path} {snippet}".strip() if snippet else path)
        for symbol_context in list(repo_hits.get("symbol_contexts", []) or [])[:2]:
            if not isinstance(symbol_context, dict):
                continue
            symbol = str(symbol_context.get("symbol") or "").strip()
            definitions = list(symbol_context.get("definitions", []) or [])
            references = list(symbol_context.get("references", []) or [])
            if symbol:
                fragments.append(f"symbol:{symbol} defs={len(definitions)} refs={len(references)}")
        return "\n".join(fragments)
