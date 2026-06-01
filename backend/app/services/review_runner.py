from __future__ import annotations

import gc
import json
import os
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.config import settings
from app.domain.models.event import ReviewEvent
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.review_rule import CandidateFinding, ReviewRuleCard, ReviewRuleCheckResult
from app.repositories.storage_factory import StorageRepositoryFactory
from app.services.artifact_service import ArtifactService, build_report_summary
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.expert_capability_service import ExpertCapabilityService
from app.services.expert_registry import ExpertRegistry
from app.services.feedback_learner_service import FeedbackLearnerService
from app.services.review_learning_service import ReviewLearningService
from app.services.code_observation_extractor import CodeObservationExtractor
from app.services.change_impact_report_service import ChangeImpactReportService
from app.services.cross_file_impact import build_cross_file_impact_hints
from app.services.code_graph.context_planner import CodeGraphContextPlanner
from app.services.code_graph.index_service import CodeGraphIndexService
from app.services.code_graph.java_tree_sitter_parser import JavaTreeSitterParser
from app.services.code_graph.storage import CodeGraphStorage
from app.services.knowledge_service import KnowledgeService
from app.services.gitnexus_impact_service import GitNexusImpactService
from app.services.llm_chat_service import LLMChatService
from app.services.main_agent_service import MainAgentService
from app.services.memory_probe import MemoryProbe
from app.services.model_prompt_profiles import resolve_model_prompt_profile
from app.services.orchestrator.graph import build_review_graph
from app.services.prompt_budget_planner import PromptBudgetPlanner
from app.services.repository_config_resolver import RepositoryConfigResolver
from app.services.repository_context_service import RepositoryContextService
from app.services.repo_review_policy_service import RepoReviewPolicyService
from app.services.review_candidate_verification_service import verify_candidate_finding
from app.services.review_environment_preflight_service import run_review_environment_preflight
from app.services.review_prompt_context_compaction import build_prompt_graph_facts
from app.services.review_workspace_service import ReviewWorkspaceService
from app.services.review_skill_activation_service import ReviewSkillActivationService
from app.services.review_skill_registry import ReviewSkillRegistry
from app.services.review_runner_common import ReviewRunnerCommonMixin
from app.services.review_runner_expert_output import ReviewRunnerExpertOutputMixin
from app.services.review_runner_issue_validation import ReviewRunnerIssueValidationMixin
from app.services.review_runner_prompting import ReviewRunnerPromptingMixin
from app.services.review_runner_rendering import ReviewRunnerRenderingMixin
from app.services.runtime_settings_service import RuntimeSettingsService
from app.services.tool_gateway import ReviewToolGateway

logger = logging.getLogger(__name__)

FALLBACK_EXPERT_ID = "architecture_design"
DDD_ARCHITECTURE_EXPERT_IDS = {"ddd_architecture", "ddd_specification"}
CHANGE_IMPACT_EXPERT_ID = "change_impact_analysis"
THOROUGH_REVIEW_CORE_EXPERT_IDS = (
    "correctness_business",
    "security_compliance",
    "performance_reliability",
    "database_analysis",
    "architecture_design",
    "ddd_architecture",
    "maintainability_code_health",
    "test_verification",
)


class ReviewClosedError(RuntimeError):
    """表示审核任务被用户主动关闭，应立即停止后续执行。"""


class ReviewRunner(
    ReviewRunnerCommonMixin,
    ReviewRunnerExpertOutputMixin,
    ReviewRunnerIssueValidationMixin,
    ReviewRunnerPromptingMixin,
    ReviewRunnerRenderingMixin,
):
    """审核执行引擎。

    这是后端最核心的运行时之一，负责把一次代码审核真正跑起来：
    - 选择专家
    - 主 Agent 派工
    - 专家调用运行时工具并产出 finding
    - graph/judge 收敛 issue
    - human gate / 最终报告落盘
    """

    def __init__(self, storage_root: Path | None = None) -> None:
        self.storage_root = Path(storage_root or settings.STORAGE_ROOT)
        repository_factory = StorageRepositoryFactory(self.storage_root)
        self.review_repo = repository_factory.create_review_repository()
        self.event_repo = repository_factory.create_event_repository()
        self.finding_repo = repository_factory.create_finding_repository()
        self.issue_repo = repository_factory.create_issue_repository()
        self.message_repo = repository_factory.create_message_repository()
        self.registry = ExpertRegistry(self.storage_root / "experts")
        self.runtime_settings_service = RuntimeSettingsService(self.storage_root)
        self.repository_resolver = RepositoryConfigResolver()
        self.review_workspace_service = ReviewWorkspaceService(self.storage_root, self.repository_resolver)
        self.review_policy_service = RepoReviewPolicyService()
        self.artifact_service = ArtifactService(self.storage_root)
        self.diff_excerpt_service = DiffExcerptService()
        self.capability_service = ExpertCapabilityService()
        self.main_agent_service = MainAgentService()
        self.llm_chat_service = LLMChatService()
        self.java_quality_signal_extractor = CodeObservationExtractor()
        self.gitnexus_impact_service = GitNexusImpactService(self.storage_root)
        self.change_impact_report_service = ChangeImpactReportService()
        self.code_graph_context_planner = CodeGraphContextPlanner()
        self.review_tool_gateway = ReviewToolGateway(self.storage_root)
        self.review_skill_registry = ReviewSkillRegistry(Path(__file__).resolve().parents[3] / "extensions" / "skills")
        self.review_skill_activation_service = ReviewSkillActivationService()
        self.feedback_learner_service = FeedbackLearnerService(self.storage_root)
        self.review_learning_service = ReviewLearningService(self.storage_root)
        self.knowledge_service = KnowledgeService(self.storage_root)
        self.knowledge_service.bootstrap_builtin_documents()
        self.graph = build_review_graph()
        self.prompt_budget_planner = PromptBudgetPlanner()
        self._knowledge_runtime_cache: dict[tuple[str, str, str, str], dict[str, object]] = {}
        self._source_excerpt_cache: dict[tuple[object, str, int, int], str] = {}
        self._target_diff_cache: dict[tuple[object, str], str] = {}
        self._related_diff_cache: dict[tuple[object, str], str] = {}
        self._problem_context_cache: dict[tuple[object, str, int, int, int, tuple[int, ...]], dict[str, object]] = {}
        self._last_gc_at = 0.0
        self._gc_interval_seconds = max(30.0, float(os.getenv("REVIEW_GC_INTERVAL_SECONDS", "60") or 60))

    def bootstrap_demo_review(self) -> str:
        review_id = f"rev_{uuid4().hex[:8]}"
        demo_file_path = "src/demo/example_service.py"
        task = ReviewTask(
            review_id=review_id,
            status="pending",
            subject=ReviewSubject(
                subject_type="mr",
                repo_id="repo_demo",
                project_id="proj_demo",
                source_ref="feature/demo",
                target_ref="main",
                title="Demo review",
                changed_files=[demo_file_path],
                unified_diff=(
                    f"diff --git a/{demo_file_path} b/{demo_file_path}\n"
                    f"--- a/{demo_file_path}\n"
                    f"+++ b/{demo_file_path}\n"
                    "@@ -1,2 +1,3 @@\n"
                    " def process(payload):\n"
                    "+    trace_id = payload.get('trace_id')\n"
                    "     return True\n"
                ),
            ),
            selected_experts=settings.DEFAULT_EXPERT_IDS,
        )
        self.review_repo.save(task)
        return review_id

    def list_events(self, review_id: str) -> list[ReviewEvent]:
        return self.event_repo.list(review_id)

    def _safe_duration_seconds(self, started_at: datetime | None, completed_at: datetime | None) -> float | None:
        """Return duration while tolerating mixed naive/aware datetimes from legacy rows."""

        if started_at is None or completed_at is None:
            return None
        safe_started = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
        safe_completed = completed_at if completed_at.tzinfo is not None else completed_at.replace(tzinfo=UTC)
        return max(0.0, round((safe_completed - safe_started).total_seconds(), 3))

    def _load_repo_review_policy(self, runtime_settings: object, subject: ReviewSubject) -> dict[str, object]:
        try:
            repository = self.repository_resolver.resolve(runtime_settings, subject)  # type: ignore[arg-type]
        except Exception:
            logger.exception("failed to resolve repository for review policy repo_id=%s", subject.repo_id)
            return self.review_policy_service.load_for_files("", list(subject.changed_files or []))
        return self.review_policy_service.load_for_files(
            repository.local_path,
            list(subject.changed_files or []),
        )

    def run_once(self, review_id: str) -> ReviewTask:
        """完整执行一次审核主链。"""
        self._knowledge_runtime_cache.clear()
        review = self.review_repo.get(review_id)
        if review is None:
            raise KeyError(review_id)
        MemoryProbe.log("review_runner.start", review_id=review_id)
        self._abort_if_closed(review_id)
        self._knowledge_runtime_cache.clear()
        self._source_excerpt_cache.clear()
        self._target_diff_cache.clear()
        self._related_diff_cache.clear()
        self._problem_context_cache.clear()
        self._source_excerpt_cache.clear()
        self._target_diff_cache.clear()
        self._related_diff_cache.clear()
        self._problem_context_cache.clear()

        review.status = "running"
        review.phase = "expert_review"
        if review.started_at is None:
            review.started_at = datetime.now(UTC)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="review_started",
                phase="intake",
                message="代码审核任务已启动",
            )
        )
        self._abort_if_closed(review_id)

        runtime_settings = self.runtime_settings_service.get()
        review = self._prepare_review_workspace(review, runtime_settings)
        analysis_mode = self._resolve_analysis_mode(review, runtime_settings)
        effective_runtime_settings = self._effective_runtime_settings(runtime_settings, analysis_mode)
        llm_request_options = self._build_llm_request_options(effective_runtime_settings, analysis_mode)
        review = self._attach_environment_preflight(review, effective_runtime_settings)
        subject_metadata = dict(review.subject.metadata or {})
        if (
            not list(review.subject.changed_files or [])
            and not str(review.subject.unified_diff or "").strip()
            and not bool(subject_metadata.get("allow_empty_diff_fallback"))
        ):
            reason = "无法继续审核：当前未获取到任何变更文件或 diff 片段。"
            review.status = "failed"
            review.phase = "failed"
            review.failure_reason = reason
            review.report_summary = reason
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = self._safe_duration_seconds(review.started_at or review.created_at, review.completed_at)
            review.updated_at = datetime.now(UTC)
            self.review_repo.save(review)
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="review_failed",
                    phase="failed",
                    message=reason,
                    payload={"changed_files": list(review.subject.changed_files or [])},
                )
            )
            return review
        review_policy = self._load_repo_review_policy(runtime_settings, review.subject)
        requested_selected_ids = [
            item for item in review.selected_experts if isinstance(item, str) and item.strip()
        ]
        manual_expert_selection = bool(dict(review.subject.metadata or {}).get("manual_expert_selection"))
        enabled_experts = self.registry.list_enabled()
        selection_started_at = time.perf_counter()
        if requested_selected_ids and manual_expert_selection:
            selection_plan = self._build_manual_expert_selection_plan(
                requested_expert_ids=requested_selected_ids,
                enabled_experts=enabled_experts,
            )
        else:
            selection_plan = self.main_agent_service.select_review_experts(
                review.subject,
                enabled_experts,
                effective_runtime_settings,
                requested_expert_ids=requested_selected_ids,
            )
        pre_policy_selection_mode = str((selection_plan.get("llm") or {}).get("mode") or "").strip().lower()
        selection_plan = self._ensure_required_mr_experts(
            subject=review.subject,
            selection_plan=selection_plan,
            enabled_experts=enabled_experts,
        )
        if pre_policy_selection_mode != "user_selected_direct":
            selection_plan = self.review_policy_service.apply_to_selection_plan(
                selection_plan,
                review_policy,
                enabled_expert_ids=[expert.expert_id for expert in enabled_experts],
            )
        selection_plan = self._ensure_thorough_review_core_experts(
            subject=review.subject,
            selection_plan=selection_plan,
            enabled_experts=enabled_experts,
            runtime_settings=effective_runtime_settings,
        )
        MemoryProbe.log(
            "review_runner.after_expert_selection",
            review_id=review.review_id,
            selected_expert_count=len(list(selection_plan.get("selected_expert_ids", []) or [])),
        )
        selection_elapsed_ms = round((time.perf_counter() - selection_started_at) * 1000, 1)
        selection_mode = str((selection_plan.get("llm") or {}).get("mode") or "").strip().lower()
        selected_ids = [
            expert_id
            for expert_id in list(selection_plan.get("selected_expert_ids", []) or [])
            if isinstance(expert_id, str) and expert_id.strip()
        ]
        experts = [expert for expert in enabled_experts if expert.expert_id in selected_ids]
        impact_analysis_expert = next((expert for expert in experts if expert.expert_id == CHANGE_IMPACT_EXPERT_ID), None)
        review_experts = [expert for expert in experts if expert.expert_id != CHANGE_IMPACT_EXPERT_ID]
        review.selected_experts = selected_ids
        review.subject.metadata = {
            **review.subject.metadata,
            "expert_selection": {
                "requested_expert_ids": list(selection_plan.get("requested_expert_ids", []) or []),
                "candidate_expert_ids": list(selection_plan.get("candidate_expert_ids", []) or []),
                "selected_experts": list(selection_plan.get("selected_experts", []) or []),
                "skipped_experts": list(selection_plan.get("skipped_experts", []) or []),
                "llm": dict(selection_plan.get("llm") or {}),
                "review_policy": dict(selection_plan.get("review_policy") or {}),
            },
            "review_policy": review_policy,
        }
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self._abort_if_closed(review_id)
        selection_summary = self._build_expert_selection_summary(selection_plan)
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="main_agent_expert_selection",
                content=selection_summary,
                metadata={
                    "phase": "coordination",
                    "selection_elapsed_ms": selection_elapsed_ms,
                    "requested_expert_ids": list(selection_plan.get("requested_expert_ids", []) or []),
                    "candidate_expert_ids": list(selection_plan.get("candidate_expert_ids", []) or []),
                    "selected_experts": list(selection_plan.get("selected_experts", []) or []),
                    "skipped_experts": list(selection_plan.get("skipped_experts", []) or []),
                    **dict(selection_plan.get("llm") or {}),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="main_agent_expert_selection",
                phase="coordination",
                message=(
                    "用户已手动选择专家，本轮将直接按用户选择执行审核"
                    if selection_mode == "user_selected_direct"
                    else "主Agent 已基于 MR 信息和专家画像确定本次参与审核的专家"
                ),
                payload={
                    "selection_elapsed_ms": selection_elapsed_ms,
                    "selected_expert_ids": selected_ids,
                    "requested_expert_ids": list(selection_plan.get("requested_expert_ids", []) or []),
                },
            )
        )
        logger.info(
            "main agent expert selection done review_id=%s analysis_mode=%s selected_experts=%s elapsed_ms=%s",
            review.review_id,
            analysis_mode,
            selected_ids,
            selection_elapsed_ms,
        )
        logger.info(
            "review execution review_id=%s analysis_mode=%s requested_experts=%s selected_experts=%s enabled_experts=%s matched_experts=%s llm_timeout=%s llm_retries=%s max_parallel=%s",
            review.review_id,
            analysis_mode,
            requested_selected_ids,
            selected_ids,
            [expert.expert_id for expert in enabled_experts],
            [expert.expert_id for expert in review_experts],
            llm_request_options["timeout_seconds"],
            llm_request_options["max_attempts"],
            self._max_parallel_experts(effective_runtime_settings, analysis_mode),
        )
        if not review_experts and impact_analysis_expert is None:
            reason = (
                "没有可执行的专家，请检查预置专家是否已部署，或确认 selected_experts 与 enabled experts 是否匹配。"
            )
            logger.error(
                "review has no executable experts review_id=%s selected_experts=%s enabled_experts=%s",
                review.review_id,
                selected_ids,
                [expert.expert_id for expert in enabled_experts],
            )
            review.status = "failed"
            review.phase = "failed"
            review.failure_reason = reason
            review.report_summary = reason
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = self._safe_duration_seconds(
                review.started_at or review.created_at,
                review.completed_at,
            )
            review.updated_at = datetime.now(UTC)
            self.review_repo.save(review)
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="review_failed",
                    phase="failed",
                    message=reason,
                    payload={
                        "selected_experts": selected_ids,
                        "enabled_experts": [expert.expert_id for expert in enabled_experts],
                    },
                )
            )
            return review

        experts_by_id = {expert.expert_id: expert for expert in review_experts}
        finding_payloads: list[dict[str, object]] = []
        expert_jobs: list[dict[str, object]] = []
        skipped_experts: list[dict[str, object]] = []
        effective_experts: list[dict[str, object]] = []
        system_added_experts: list[dict[str, object]] = []
        intake_summary, intake_metadata = self.main_agent_service.build_intake_summary(review.subject)
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="main_agent_intake",
                content=intake_summary,
                metadata={
                    "phase": "coordination",
                    **intake_metadata,
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="main_agent_intake",
                phase="coordination",
                message="主Agent 已播报本次审核输入信息",
                payload=intake_metadata,
            )
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="main_agent_routing_preparing",
                content="主Agent 正在构建派工上下文：扫描候选 hunk、检索代码仓上下文，并生成专家派工计划。",
                metadata={
                    "phase": "coordination",
                    "selected_expert_ids": selected_ids,
                    "analysis_mode": analysis_mode,
                    "changed_file_count": len(review.subject.changed_files),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="main_agent_routing_preparing",
                phase="coordination",
                message="主Agent 正在构建派工上下文",
                payload={
                    "selected_expert_ids": selected_ids,
                    "analysis_mode": analysis_mode,
                    "changed_file_count": len(review.subject.changed_files),
                },
            )
        )
        routing_started_at = time.perf_counter()
        if not review_experts:
            routing_plan = {}
        elif selection_mode == "user_selected_direct":
            routing_plan = self._build_manual_routing_plan(review.subject, review_experts)
        else:
            routing_plan = self.main_agent_service.build_routing_plan(
                review.subject,
                review_experts,
                effective_runtime_settings,
                analysis_mode=analysis_mode,
            )
        MemoryProbe.log(
            "review_runner.after_routing_plan",
            review_id=review.review_id,
            routed_expert_count=len(routing_plan),
        )
        routing_elapsed_ms = round((time.perf_counter() - routing_started_at) * 1000, 1)
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="main_agent_routing_ready",
                content=f"主Agent 已完成派工规划，用时 {routing_elapsed_ms} ms，开始向专家下发任务。",
                metadata={
                    "phase": "coordination",
                    "analysis_mode": analysis_mode,
                    "routing_elapsed_ms": routing_elapsed_ms,
                    "selected_expert_ids": selected_ids,
                    "changed_file_count": len(review.subject.changed_files),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="main_agent_routing_ready",
                phase="coordination",
                message="主Agent 已完成派工计划，开始向专家下发任务",
                payload={
                    "routing_elapsed_ms": routing_elapsed_ms,
                    "selected_expert_ids": selected_ids,
                    "analysis_mode": analysis_mode,
                },
            )
        )
        logger.info(
            "main agent routing ready review_id=%s analysis_mode=%s selected_experts=%s elapsed_ms=%s",
            review.review_id,
            analysis_mode,
            selected_ids,
            routing_elapsed_ms,
        )
        candidate_hunks = self.main_agent_service.build_candidate_hunks(
            review.subject,
            effective_runtime_settings,
        )
        if impact_analysis_expert is not None:
            review = self._run_change_impact_analysis_flow(
                review=review,
                expert=impact_analysis_expert,
                runtime_settings=effective_runtime_settings,
            )
        for expert in review_experts:
            expert_id = expert.expert_id
            primary_route = dict(routing_plan.get(expert_id) or {})
            file_path = str(primary_route.get("file_path") or self._pick_file_path(review.subject, expert_id))
            line_start = int(primary_route.get("line_start") or 1)
            llm_metadata = dict(primary_route.get("routing_llm") or {})
            expert_route_jobs: list[dict[str, object]] = []
            if self._should_skip_expert_route(
                subject=review.subject,
                primary_route=primary_route,
                runtime_settings=effective_runtime_settings,
            ):
                skip_reason = str(primary_route.get("skip_reason") or "当前变更未命中该专家的有效审查线索")
                skipped_experts.append(
                    {
                        "expert_id": expert_id,
                        "expert_name": expert.name_zh,
                        "reason": skip_reason,
                        "file_path": file_path,
                        "line_start": line_start,
                    }
                )
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review_id,
                        event_type="expert_skipped",
                        phase="coordination",
                        message=f"{expert.name_zh} 已跳过本轮审查",
                        payload={
                            "expert_id": expert_id,
                            "file_path": file_path,
                            "line_start": line_start,
                            "reason": skip_reason,
                        },
                    )
                )
                self.message_repo.append(
                    ConversationMessage(
                        review_id=review_id,
                        issue_id="review_orchestration",
                        expert_id=expert_id,
                        message_type="expert_skipped",
                        content=f"{expert.name_zh} 已跳过本轮审查：{skip_reason}",
                        metadata={
                            "phase": "coordination",
                            "file_path": file_path,
                            "line_start": line_start,
                            "reason": skip_reason,
                            **llm_metadata,
                        },
                    )
                )
                continue
            if not bool(primary_route.get("routeable", True)):
                skip_reason = str(primary_route.get("skip_reason") or "路由模型未给出可审查目标")
                primary_route["routeable"] = True
                primary_route["routing_reason"] = (
                    f"深度检视模式覆盖路由跳过结论：{skip_reason}。"
                    "该模式要求入选专家覆盖全部候选 hunk，避免弱路由模型漏检。"
                )
                primary_route["routing_skip_overridden"] = True
                llm_metadata["routing_skip_overridden"] = True
                llm_metadata["routing_skip_reason"] = skip_reason
            effective_experts.append(
                {
                    "expert_id": expert_id,
                    "expert_name": expert.name_zh,
                    "source": "user_selected",
                    "file_path": file_path,
                    "line_start": line_start,
                }
            )
            route_hints = self._build_expert_route_hints(
                review.subject,
                expert,
                candidate_hunks,
                primary_route=primary_route,
            )
            for route_index, route_hint in enumerate(route_hints, start=1):
                command = self.main_agent_service.build_command(
                    review.subject,
                    expert,
                    effective_runtime_settings,
                    route_hint=route_hint,
                )
                hunk_file_path = str(command.get("file_path") or file_path)
                hunk_line_start = int(command.get("line_start") or line_start or 1)
                summary = str(command.get("summary") or "")
                raw_repository_context = dict(command.get("repository_context") or {})
                target_hunk_payload = dict(command.get("target_hunk") or {})
                enriched_repository_context = self._augment_repository_context_with_quality_signals(
                    review.subject,
                    hunk_file_path,
                    hunk_line_start,
                    raw_repository_context,
                    target_hunk_payload,
                )
                code_graph_bundle = self._build_code_graph_context_bundle(
                    review=review,
                    runtime_settings=effective_runtime_settings,
                    file_path=hunk_file_path,
                    repository_context=enriched_repository_context,
                )
                if code_graph_bundle:
                    self._record_code_graph_context_bundle(review_id, code_graph_bundle)
                    enriched_repository_context = self._merge_code_graph_context_bundle(
                        enriched_repository_context,
                        code_graph_bundle,
                    )
                command_message = self.message_repo.append(
                    ConversationMessage(
                        review_id=review_id,
                        issue_id="review_orchestration",
                        expert_id=self.main_agent_service.agent_id,
                        message_type="main_agent_command",
                        content=summary,
                        metadata={
                            "phase": "coordination",
                            "target_expert_id": expert_id,
                            "target_expert_name": expert.name_zh,
                            "file_path": hunk_file_path,
                            "line_start": hunk_line_start,
                            "hunk_index": route_index,
                            "hunk_count": len(route_hints),
                            "related_files": command.get("related_files", []),
                            "business_changed_files": self._business_changed_files(review.subject),
                            "target_hunk": target_hunk_payload,
                            "repository_context": self._build_repository_context_metadata(enriched_repository_context),
                            "expected_checks": command.get("expected_checks", []),
                            "disallowed_inference": command.get("disallowed_inference", []),
                            "routing_reason": command.get("routing_reason", ""),
                            "routing_confidence": command.get("routing_confidence", 0.0),
                            **llm_metadata,
                        },
                    )
                )
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review_id,
                        event_type="main_agent_command",
                        phase="coordination",
                        message=f"主Agent 已向 {expert.name_zh} 下发第 {route_index}/{len(route_hints)} 个 hunk 审查指令",
                        payload={
                            "target_expert_id": expert_id,
                            "target_expert_name": expert.name_zh,
                            "file_path": hunk_file_path,
                            "line_start": hunk_line_start,
                            "hunk_index": route_index,
                            "hunk_count": len(route_hints),
                            "related_files": command.get("related_files", []),
                            "business_changed_files": self._business_changed_files(review.subject),
                        },
                    )
                )
                knowledge_context = self._build_knowledge_review_context(
                    review.subject,
                    expert,
                    hunk_file_path,
                    hunk_line_start,
                    enriched_repository_context,
                    target_hunk_payload,
                )
                expert_route_jobs.append(
                    {
                        "review": review,
                        "expert": expert,
                        "command_message": command_message,
                        "file_path": hunk_file_path,
                        "line_start": hunk_line_start,
                        "repository_context": enriched_repository_context,
                        "target_hunk": target_hunk_payload,
                        "target_hunks": [dict(item) for item in list(command.get("target_hunks") or []) if isinstance(item, dict)],
                        "related_files": list(command.get("related_files") or []),
                        "business_changed_files": self._business_changed_files(review.subject),
                        "expected_checks": list(command.get("expected_checks") or []),
                        "disallowed_inference": list(command.get("disallowed_inference") or []),
                        "routing_reason": str(command.get("routing_reason") or ""),
                        "routing_confidence": float(command.get("routing_confidence") or 0.0),
                        "runtime_settings": effective_runtime_settings,
                        "analysis_mode": analysis_mode,
                        "llm_request_options": llm_request_options,
                        "bound_documents": [],
                        "knowledge_context": knowledge_context,
                        "rule_screening": {},
                        "finding_payloads": finding_payloads,
                        "code_graph_priority_score": self._code_graph_priority_score(
                            enriched_repository_context,
                            hunk_file_path,
                            hunk_line_start,
                        ),
                    }
                )
            if expert_route_jobs:
                bound_documents, rule_screening = self._prepare_expert_batch_knowledge_inputs(
                    review_id=review.review_id,
                    expert_id=expert.expert_id,
                    analysis_mode=analysis_mode,
                    route_jobs=expert_route_jobs,
                    runtime_settings=effective_runtime_settings,
                )
                logger.info(
                    "expert batch rule screening prepared review_id=%s expert_id=%s file_count=%s hunk_count=%s total_rules=%s matched_rule_count=%s must_review=%s possible_hit=%s matched_rule_ids=%s",
                    review.review_id,
                    expert.expert_id,
                    len(
                        {
                            str(item.get("file_path") or "").strip()
                            for item in expert_route_jobs
                            if str(item.get("file_path") or "").strip()
                        }
                    ),
                    sum(
                        len([hunk for hunk in list(item.get("target_hunks") or []) if isinstance(hunk, dict)]) or 1
                        for item in expert_route_jobs
                    ),
                    int(rule_screening.get("total_rules") or 0),
                    int(rule_screening.get("matched_rule_count") or 0),
                    int(rule_screening.get("must_review_count") or 0),
                    int(rule_screening.get("possible_hit_count") or 0),
                    [
                        str(item.get("rule_id") or "").strip()
                        for item in list(rule_screening.get("matched_rules_for_llm", []) or [])[:8]
                    ],
                )
                for job in expert_route_jobs:
                    job["bound_documents"] = list(bound_documents)
                    job["rule_screening"] = dict(rule_screening or {})
            expert_jobs.extend(
                self._batch_expert_jobs(
                    expert_route_jobs,
                    runtime_settings=effective_runtime_settings,
                    analysis_mode=analysis_mode,
                )
            )

        fallback_job = self._maybe_build_fallback_job(
            review=review,
            enabled_experts=enabled_experts,
            existing_jobs=expert_jobs,
            selected_ids=selected_ids,
            skipped_experts=skipped_experts,
            effective_runtime_settings=effective_runtime_settings,
            analysis_mode=analysis_mode,
            llm_request_options=llm_request_options,
            finding_payloads=finding_payloads,
        )
        if fallback_job is not None:
            expert_jobs.append(fallback_job)
            fallback_expert = fallback_job["expert"]
            assert isinstance(fallback_expert, ExpertProfile)
            file_path = str(fallback_job["file_path"])
            line_start = int(fallback_job["line_start"])
            system_added_experts.append(
                {
                    "expert_id": fallback_expert.expert_id,
                    "expert_name": fallback_expert.name_zh,
                    "reason": "用户选择的专家与当前变更相关性不足，已自动补入架构与设计专家做兜底审查",
                    "file_path": file_path,
                    "line_start": line_start,
                }
            )
            effective_experts.append(
                {
                    "expert_id": fallback_expert.expert_id,
                    "expert_name": fallback_expert.name_zh,
                    "source": "system_fallback",
                    "file_path": file_path,
                    "line_start": line_start,
                }
            )

        expert_jobs = self._sort_expert_jobs_by_code_graph_priority(expert_jobs)
        routing_summary = self._build_routing_summary(
            selected_ids=requested_selected_ids,
            experts_by_id={expert.expert_id: expert for expert in enabled_experts},
            skipped_experts=skipped_experts,
            effective_experts=effective_experts,
            system_added_experts=system_added_experts,
        )
        review = self._merge_review_metadata(review, {"expert_routing": routing_summary})
        self._abort_if_closed(review_id)
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="expert_routing_summary",
                phase="coordination",
                message=self._build_routing_summary_message(routing_summary),
                payload=routing_summary,
            )
        )

        expert_execution_started_at = time.perf_counter()
        expert_failures = self._execute_expert_jobs(expert_jobs, effective_runtime_settings, analysis_mode) or []
        MemoryProbe.log(
            "review_runner.after_expert_jobs",
            review_id=review.review_id,
            expert_job_count=len(expert_jobs),
            expert_failure_count=len(expert_failures),
        )
        expert_execution_elapsed_ms = round((time.perf_counter() - expert_execution_started_at) * 1000, 1)
        review = self._merge_review_metadata(
            review,
            {
                "expert_execution": {
                    "failed_experts": expert_failures,
                    "partial_failure_count": len(expert_failures),
                    "successful_expert_job_count": max(0, len(expert_jobs) - len(expert_failures)),
                    "expert_job_count": len(expert_jobs),
                    "analysis_mode": analysis_mode,
                }
            },
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="main_agent_expert_execution_completed",
                content=f"专家并行审查阶段已完成，用时 {expert_execution_elapsed_ms} ms，共执行 {len(expert_jobs)} 个专家任务。",
                metadata={
                    "phase": "coordination",
                    "analysis_mode": analysis_mode,
                    "expert_execution_elapsed_ms": expert_execution_elapsed_ms,
                    "expert_job_count": len(expert_jobs),
                    "selected_expert_ids": selected_ids,
                },
            )
        )
        if expert_failures:
            self.message_repo.append(
                ConversationMessage(
                    review_id=review_id,
                    issue_id="review_orchestration",
                    expert_id=self.main_agent_service.agent_id,
                    message_type="main_agent_expert_execution_partial_failure",
                    content=f"本轮有 {len(expert_failures)} 个专家任务执行失败，系统已保留其余专家的发现并继续收敛结果。",
                    metadata={
                        "phase": "coordination",
                        "analysis_mode": analysis_mode,
                        "expert_failures": expert_failures,
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="expert_execution_partial_failure",
                    phase="coordination",
                    message="部分专家任务执行失败，系统将保留其余专家结果继续收敛。",
                    payload={"expert_failures": expert_failures},
                )
            )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="main_agent_expert_execution_completed",
                phase="coordination",
                message="专家审查执行阶段已完成",
                payload={
                    "expert_execution_elapsed_ms": expert_execution_elapsed_ms,
                    "expert_job_count": len(expert_jobs),
                    "selected_expert_ids": selected_ids,
                },
            )
        )
        logger.info(
            "expert execution completed review_id=%s analysis_mode=%s expert_job_count=%s elapsed_ms=%s",
            review.review_id,
            analysis_mode,
            len(expert_jobs),
            expert_execution_elapsed_ms,
        )
        self._append_deterministic_query_bound_findings(review, finding_payloads)
        self._append_deterministic_java_quality_findings(review, finding_payloads)
        self._append_deterministic_observation_findings(review, expert_jobs, finding_payloads)
        self._append_empty_diff_fallback_finding(
            review,
            expert_jobs,
            finding_payloads,
            manual_expert_selection=manual_expert_selection,
        )
        self._abort_if_closed(review_id)
        if not expert_jobs and impact_analysis_expert is None:
            reason = "用户选择的专家与当前变更相关性不足，且未能补入兜底专家，无法继续审核。"
            logger.error(
                "review has no executable expert jobs review_id=%s changed_files=%s remote_diff_available=%s skipped_experts=%s",
                review.review_id,
                list(review.subject.changed_files),
                bool(review.subject.unified_diff),
                [item["expert_id"] for item in skipped_experts],
            )
            review.status = "failed"
            review.phase = "failed"
            review.failure_reason = reason
            review.report_summary = reason
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = self._safe_duration_seconds(
                review.started_at or review.created_at,
                review.completed_at,
            )
            review.updated_at = datetime.now(UTC)
            self.review_repo.save(review)
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="review_failed",
                    phase="failed",
                    message=reason,
                    payload={
                        "changed_files": list(review.subject.changed_files),
                        "remote_diff_available": bool(review.subject.unified_diff),
                        "expert_routing": routing_summary,
                    },
                )
            )
            return review

        graph_result = self.graph.invoke(
            {
                "review_id": review_id,
                "phase": "ingest",
                "subject_type": review.subject.subject_type,
                "analysis_mode": analysis_mode,
                "changed_files": review.subject.changed_files,
                "unified_diff": review.subject.unified_diff,
                "selected_experts": selected_ids,
                "issue_filter_config": {
                    "issue_filter_enabled": bool(getattr(runtime_settings, "issue_filter_enabled", True)),
                    "issue_min_priority_level": str(
                        getattr(runtime_settings, "issue_min_priority_level", "P3") or "P3"
                    ).upper(),
                    "issue_confidence_threshold_p0": float(
                        getattr(runtime_settings, "issue_confidence_threshold_p0", 0.9) or 0.9
                    ),
                    "issue_confidence_threshold_p1": float(
                        getattr(runtime_settings, "issue_confidence_threshold_p1", 0.75) or 0.75
                    ),
                    "issue_confidence_threshold_p2": float(
                        getattr(runtime_settings, "issue_confidence_threshold_p2", 0.55) or 0.55
                    ),
                    "issue_confidence_threshold_p3": float(
                        getattr(runtime_settings, "issue_confidence_threshold_p3", 0.45) or 0.45
                    ),
                    "suppress_low_risk_hint_issues": bool(
                        getattr(runtime_settings, "suppress_low_risk_hint_issues", False)
                    ),
                    "hint_issue_confidence_threshold": float(
                        getattr(runtime_settings, "hint_issue_confidence_threshold", 0.7) or 0.7
                    ),
                    "hint_issue_evidence_cap": max(
                        0,
                        int(getattr(runtime_settings, "hint_issue_evidence_cap", 2) or 2),
                    ),
                },
                "findings": finding_payloads,
                "feedback_quality_profiles": self.feedback_learner_service.build_quality_profiles(),
                "runtime_settings": effective_runtime_settings,
                "review_policy": review_policy,
            }
        )

        issue_filter_decisions = [
            item
            for item in list(graph_result.get("issue_filter_decisions", []))
            if isinstance(item, dict)
        ]
        learning_filtered_issues, issue_filter_decisions = self._apply_review_learning_case_judgement(
            repo_id=review.subject.repo_id,
            issues=[
                dict(item)
                for item in list(graph_result.get("issues", []))
                if isinstance(item, dict)
            ],
            issue_filter_decisions=issue_filter_decisions,
        )
        graph_result["issues"] = learning_filtered_issues
        filtered_finding_ids = {
            str(finding_id)
            for decision in issue_filter_decisions
            for finding_id in list(decision.get("finding_ids") or [])
            if str(finding_id).strip()
        }

        issues = [
            DebateIssue(
                review_id=review_id,
                issue_id=str(item.get("issue_id") or f"iss_{uuid4().hex[:12]}"),
                title=str(item.get("title") or "待裁决议题"),
                summary=str(item.get("summary") or ""),
                finding_type=str(item.get("finding_type") or "risk_hypothesis"),
                normalized_issue_type=str(item.get("normalized_issue_type") or ""),
                risk_domain=str(item.get("risk_domain") or ""),
                primary_expert_id=str(item.get("primary_expert_id") or ""),
                file_path=str(item.get("file_path") or ""),
                line_start=int(item.get("line_start") or 1),
                method_name=str(item.get("method_name") or ""),
                code_anchor=str(item.get("code_anchor") or ""),
                evidence_anchor_status=str(item.get("evidence_anchor_status") or "unchecked"),
                evidence_anchor_reason=str(item.get("evidence_anchor_reason") or ""),
                status=str(item.get("status") or "open"),
                severity=str(item.get("severity") or "medium"),
                confidence=float(item.get("confidence") or 0.72),
                confidence_breakdown=dict(item.get("confidence_breakdown") or {}),
                finding_ids=[str(value) for value in item.get("finding_ids", [])],
                participant_expert_ids=[str(value) for value in item.get("participant_expert_ids", [])],
                supporting_expert_ids=[str(value) for value in item.get("supporting_expert_ids", [])],
                expert_views=[dict(value) for value in item.get("expert_views", []) if isinstance(value, dict)],
                aggregated_titles=[str(value) for value in item.get("aggregated_titles", [])],
                aggregated_summaries=[str(value) for value in item.get("aggregated_summaries", [])],
                aggregated_remediation_strategies=[
                    str(value) for value in item.get("aggregated_remediation_strategies", [])
                ],
                aggregated_remediation_suggestions=[
                    str(value) for value in item.get("aggregated_remediation_suggestions", [])
                ],
                aggregated_remediation_steps=[
                    str(value) for value in item.get("aggregated_remediation_steps", [])
                ],
                remediation_strategy=str(item.get("remediation_strategy") or ""),
                remediation_suggestion=str(item.get("remediation_suggestion") or ""),
                remediation_steps=[str(value) for value in item.get("remediation_steps", [])],
                current_code=str(item.get("current_code") or ""),
                suggested_code=str(item.get("suggested_code") or ""),
                evidence=[str(value) for value in item.get("evidence", [])],
                cross_file_evidence=[str(value) for value in item.get("cross_file_evidence", [])],
                evidence_chain=[
                    dict(value) for value in item.get("evidence_chain", []) if isinstance(value, dict)
                ],
                assumptions=[str(value) for value in item.get("assumptions", [])],
                context_files=[str(value) for value in item.get("context_files", [])],
                direct_evidence=bool(item.get("direct_evidence")),
                needs_human=bool(item.get("needs_human")),
                verified=bool(item.get("verified")),
                needs_debate=bool(item.get("needs_debate")),
                verifier_name=str(item.get("verifier_name") or ""),
                tool_name=str(item.get("tool_name") or ""),
                tool_verified=bool(item.get("tool_verified")) or bool(item.get("sast_cross_validated")),
                sast_cross_validated=bool(item.get("sast_cross_validated")),
                sast_prescan_matches=[
                    dict(value) for value in list(item.get("sast_prescan_matches") or []) if isinstance(value, dict)
                ],
                resolution=str(item.get("resolution") or ""),
                consistency_check_status=str(item.get("consistency_check_status") or "unchecked"),
                consistency_check_summary=str(item.get("consistency_check_summary") or ""),
                consistency_conflicts=[str(value) for value in item.get("consistency_conflicts", [])],
                remediation_alignment_status=str(item.get("remediation_alignment_status") or "unchecked"),
                remediation_alignment_conflicts=[
                    str(value) for value in item.get("remediation_alignment_conflicts", [])
                ],
                remediation_filtered=bool(item.get("remediation_filtered")),
            )
            for item in graph_result.get("issues", [])
        ]
        fallback_candidates = [
            item
            for item in finding_payloads
            if str(item.get("finding_id") or "").strip() not in filtered_finding_ids
        ]
        eligible_fallback_candidates = [
            item
            for item in fallback_candidates
            if self._finding_can_fallback_to_issue(
                item,
                changed_files=list(review.subject.changed_files or []),
            )
        ]
        if not issues and eligible_fallback_candidates:
            fallback_source = sorted(
                eligible_fallback_candidates,
                key=lambda item: (
                    {"blocker": 4, "critical": 3, "high": 3, "medium": 2, "low": 1}.get(
                        str(item.get("severity") or "medium").lower(),
                        2,
                    ),
                    float(item.get("confidence") or 0.0),
                ),
                reverse=True,
            )[0]
            fallback_file = str(fallback_source.get("file_path") or "").strip()
            fallback_severity = str(fallback_source.get("severity") or "medium").strip().lower() or "medium"
            changed_files_lower = [str(item).lower() for item in list(review.subject.changed_files or [])]
            has_security_surface = any("security" in item or "auth" in item for item in changed_files_lower)
            needs_human = has_security_surface
            fallback_status = "needs_human" if needs_human else "needs_verification"
            fallback_resolution = "needs_human_review" if needs_human else "needs_verification"
            fallback_issue_id = f"iss_fallback_{uuid4().hex[:10]}"
            fallback_title = str(fallback_source.get("title") or "").strip() or "待核验议题"
            fallback_summary = str(fallback_source.get("summary") or "").strip() or str(
                fallback_source.get("claim") or ""
            ).strip()
            fallback_finding_id = str(fallback_source.get("finding_id") or f"fd_{uuid4().hex[:12]}")
            fallback_expert = str(fallback_source.get("expert_id") or "").strip()
            issues = [
                DebateIssue(
                    review_id=review_id,
                    issue_id=fallback_issue_id,
                    title=fallback_title,
                    summary=fallback_summary,
                    finding_type=str(fallback_source.get("finding_type") or "risk_hypothesis"),
                    normalized_issue_type=str(fallback_source.get("normalized_issue_type") or ""),
                    risk_domain=str(fallback_source.get("risk_domain") or ""),
                    primary_expert_id=fallback_expert,
                    file_path=fallback_file,
                    line_start=int(fallback_source.get("line_start") or 1),
                    method_name=str(fallback_source.get("method_name") or ""),
                    code_anchor=str(fallback_source.get("code_anchor") or fallback_source.get("code_excerpt") or ""),
                    evidence_anchor_status=str(fallback_source.get("evidence_anchor_status") or "unchecked"),
                    evidence_anchor_reason=str(fallback_source.get("evidence_anchor_reason") or ""),
                    status=fallback_status,
                    severity=fallback_severity,
                    confidence=min(0.85, max(0.55, float(fallback_source.get("confidence") or 0.0))),
                    confidence_breakdown={"source": "fallback_when_no_issue"},
                    finding_ids=[fallback_finding_id],
                    participant_expert_ids=[fallback_expert] if fallback_expert else [],
                    expert_views=(
                        [
                            {
                                "expert_id": fallback_expert,
                                "title": fallback_title,
                                "summary": fallback_summary,
                                "severity": fallback_severity,
                                "confidence": float(fallback_source.get("confidence") or 0.0),
                            }
                        ]
                        if fallback_expert
                        else []
                    ),
                    aggregated_titles=[fallback_title],
                    aggregated_summaries=[fallback_summary] if fallback_summary else [],
                    aggregated_remediation_strategies=[],
                    aggregated_remediation_suggestions=[],
                    aggregated_remediation_steps=[],
                    evidence=[str(v) for v in list(fallback_source.get("evidence") or []) if str(v).strip()],
                    cross_file_evidence=[
                        str(v) for v in list(fallback_source.get("cross_file_evidence") or []) if str(v).strip()
                    ],
                    evidence_chain=[
                        dict(v) for v in list(fallback_source.get("evidence_chain") or []) if isinstance(v, dict)
                    ],
                    assumptions=[str(v) for v in list(fallback_source.get("assumptions") or []) if str(v).strip()],
                    context_files=[str(v) for v in list(fallback_source.get("context_files") or []) if str(v).strip()],
                    direct_evidence=bool(fallback_source.get("direct_evidence")),
                    needs_human=needs_human,
                    verified=False,
                    needs_debate=False,
                    verifier_name="builtin_verifier",
                    tool_name="local_diff",
                    tool_verified=False,
                    resolution=fallback_resolution,
                )
            ]
            self.message_repo.append(
                ConversationMessage(
                    review_id=review_id,
                    issue_id=fallback_issue_id,
                    expert_id=self.main_agent_service.agent_id,
                    message_type="debate_message",
                    content="自动降级为兜底议题：issues 为空但存在 findings，已保留为可核验议题。",
                    metadata={
                        "phase": "debate",
                        "fallback_issue": True,
                        "needs_human": needs_human,
                        "file_path": fallback_file,
                    },
                )
            )
            logger.info(
                "issue fallback activated review_id=%s finding_count=%s selected_finding=%s needs_human=%s",
                review_id,
                len(finding_payloads),
                fallback_finding_id,
                needs_human,
            )
        elif not issues and fallback_candidates:
            logger.info(
                "issue fallback skipped because findings are not strong enough review_id=%s finding_count=%s",
                review_id,
                len(fallback_candidates),
            )
        elif not issues and filtered_finding_ids:
            logger.info(
                "issue fallback skipped because all findings were filtered review_id=%s finding_count=%s filtered_count=%s",
                review_id,
                len(finding_payloads),
                len(filtered_finding_ids),
            )
        self._enrich_issues_with_finding_evidence_chains(issues, finding_payloads)
        if self._should_coalesce_final_issues(effective_runtime_settings):
            issues = self._coalesce_duplicate_issues(issues)
        else:
            issues = [self._normalize_single_coalesced_issue(issue) for issue in issues]
        if issue_filter_decisions:
            self.message_repo.append(
                ConversationMessage(
                    review_id=review_id,
                    issue_id="review_orchestration",
                    expert_id=self.main_agent_service.agent_id,
                    message_type="issue_filter_applied",
                    content=f"本轮有 {len(issue_filter_decisions)} 组提示性或低风险问题被保留为 findings，未升级为 issues。",
                    metadata={
                        "phase": "coordination",
                        "decision_count": len(issue_filter_decisions),
                        "issue_filter_decisions": issue_filter_decisions,
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="issue_filter_applied",
                    phase="coordination",
                    message="治理规则已筛出仅保留为 finding 的提示性问题",
                    payload={
                        "decision_count": len(issue_filter_decisions),
                        "issue_filter_decisions": issue_filter_decisions,
                    },
                )
            )
        findings_by_id = {
            item.finding_id: item
            for item in self.finding_repo.list(review_id)
        }
        issues = self._validate_final_issues_with_judge(
            review=review,
            issues=issues,
            findings_by_id=findings_by_id,
            runtime_settings=effective_runtime_settings,
            llm_request_options=llm_request_options,
        )
        if self._should_coalesce_final_issues(effective_runtime_settings):
            issues = self._coalesce_duplicate_issues(issues)
        else:
            issues = [self._normalize_single_coalesced_issue(issue) for issue in issues]
        issues = self._filter_invalid_final_issues(review_id, issues)
        issues = self._make_final_issue_display_texts_distinct(issues)
        issues, auto_confirmed_issue_ids = self._auto_confirm_high_confidence_issues(issues)
        if auto_confirmed_issue_ids:
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="high_confidence_issues_auto_confirmed",
                    phase="judge",
                    message="高置信直证据问题已自动进入正式处理清单",
                    payload={"issue_ids": auto_confirmed_issue_ids},
                )
            )
        self.issue_repo.save_all(review_id, issues)
        for issue in issues:
            self._abort_if_closed(review_id)
            self._persist_issue_thread(
                review=review,
                issue=issue,
                experts_by_id=experts_by_id,
                runtime_settings=effective_runtime_settings,
                analysis_mode=analysis_mode,
                llm_request_options=llm_request_options,
            )

        pending_human_issue_ids = [issue.issue_id for issue in issues if issue.needs_human]
        if pending_human_issue_ids:
            analysis_completed_at = datetime.now(UTC)
            review.status = "waiting_human"
            review.phase = "human_gate"
            review.human_review_status = "requested"
            review.pending_human_issue_ids = pending_human_issue_ids
            # 自动分析到达人工门控时即结束；后续人工等待时间不计入分析耗时。
            review.completed_at = analysis_completed_at
            review.duration_seconds = self._safe_duration_seconds(
                review.started_at or review.created_at,
                analysis_completed_at,
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="human_gate_requested",
                    phase="human_gate",
                    message="高风险议题已提交人工复核",
                    payload={"issue_ids": pending_human_issue_ids},
                )
            )
        else:
            review.status = "completed"
            review.phase = "completed"
            review.human_review_status = "not_required"
            review.pending_human_issue_ids = []
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = self._safe_duration_seconds(
                review.started_at or review.created_at,
                review.completed_at,
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="review_completed",
                    phase="completed",
                    message="代码审核任务已完成",
                )
            )

        review.report_summary = build_report_summary(
            review=review,
            finding_count=len(finding_payloads),
            issue_count=len(issues),
            pending_human_count=len(pending_human_issue_ids),
            partial_failure_count=len(expert_failures),
        )
        review = self._attach_impact_report(review, effective_runtime_settings)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self._abort_if_closed(review_id)
        try:
            final_summary, final_llm = self.main_agent_service.build_final_summary(
                review,
                issues,
                effective_runtime_settings,
                partial_failure_count=len(expert_failures),
                finding_count=len(finding_payloads),
                filtered_finding_count=len(filtered_finding_ids),
                pending_human_count=len(pending_human_issue_ids),
                timeout_seconds=float(llm_request_options["timeout_seconds"]),
                max_attempts=int(llm_request_options["max_attempts"]),
            )
        except TypeError:
            # 兼容旧签名（无 partial_failure_count 参数）的测试桩与扩展实现。
            final_summary, final_llm = self.main_agent_service.build_final_summary(
                review,
                issues,
                effective_runtime_settings,
                timeout_seconds=float(llm_request_options["timeout_seconds"]),
                max_attempts=int(llm_request_options["max_attempts"]),
            )
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="main_agent_summary",
                content=final_summary,
                metadata={
                    "phase": "coordination",
                    "status": review.status,
                    "issue_count": len(issues),
                    "pending_human_count": len(pending_human_issue_ids),
                    "partial_failure_count": len(expert_failures),
                    **final_llm,
                },
            )
        )
        if not self._has_live_llm_call(review_id):
            reason = "无法完成审核：本次检视任务未产生任何真实 LLM 调用，请先配置可用模型后重试。"
            review.status = "failed"
            review.phase = "failed"
            review.failure_reason = reason
            review.report_summary = reason
            review.human_review_status = "not_required"
            review.pending_human_issue_ids = []
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = self._safe_duration_seconds(
                review.started_at or review.created_at,
                review.completed_at,
            )
            review.updated_at = datetime.now(UTC)
            self.review_repo.save(review)
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="review_failed",
                    phase="failed",
                    message=reason,
                    payload={"reason_code": "llm_live_call_required"},
                )
            )
            self.artifact_service.publish(review, issues)
            MemoryProbe.log("review_runner.finish", review_id=review.review_id, status=review.status)
            return review
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="main_agent_summary",
                phase="coordination",
                message="主Agent 已完成收敛总结",
                payload={
                    "issue_count": len(issues),
                    "pending_human_count": len(pending_human_issue_ids),
                    "status": review.status,
                    "partial_failure_count": len(expert_failures),
                },
            )
        )
        self._abort_if_closed(review_id)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        logger.info(
            "review finished review_id=%s status=%s finding_count=%s issue_count=%s pending_human=%s",
            review.review_id,
            review.status,
            len(finding_payloads),
            len(issues),
            len(pending_human_issue_ids),
        )
        self.artifact_service.publish(review, issues)
        MemoryProbe.log(
            "review_runner.finish",
            review_id=review.review_id,
            status=review.status,
            finding_count=len(finding_payloads),
            issue_count=len(issues),
        )
        return review

    def _apply_review_learning_case_judgement(
        self,
        *,
        repo_id: str,
        issues: list[dict[str, object]],
        issue_filter_decisions: list[dict[str, object]],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        filtered_issues: list[dict[str, object]] = []
        decisions = list(issue_filter_decisions)
        for issue in issues:
            learning_decision = self.review_learning_service.evaluate_issue_candidate(
                repo_id=repo_id,
                issue=issue,
                record_match=True,
            )
            action = str(learning_decision.get("action") or "keep")
            if action == "reject":
                decisions.append(
                    {
                        "issue_id": str(issue.get("issue_id") or ""),
                        "finding_ids": [
                            str(finding_id)
                            for finding_id in list(issue.get("finding_ids") or [])
                            if str(finding_id).strip()
                        ],
                        "rule_code": "review_learning_false_positive_case",
                        "rule_label": "历史人工驳回案例过滤",
                        "reason": str(learning_decision.get("reason") or "命中历史人工驳回案例，过滤该候选问题。"),
                        "matched_case_id": str(learning_decision.get("matched_case_id") or ""),
                        "similarity": float(learning_decision.get("similarity") or 0.0),
                    }
                )
                continue
            if action == "needs_verification":
                next_issue = dict(issue)
                next_issue["status"] = "needs_verification"
                next_issue["resolution"] = "review_learning_case_requires_verification"
                next_issue["needs_human"] = False
                confidence_breakdown = dict(next_issue.get("confidence_breakdown") or {})
                confidence_breakdown["review_learning_case"] = {
                    "matched_case_id": str(learning_decision.get("matched_case_id") or ""),
                    "similarity": float(learning_decision.get("similarity") or 0.0),
                    "reason": str(learning_decision.get("reason") or ""),
                }
                next_issue["confidence_breakdown"] = confidence_breakdown
                filtered_issues.append(next_issue)
                continue
            if action == "boost":
                next_issue = dict(issue)
                adjustment = float(learning_decision.get("confidence_adjustment") or 0.0)
                current_confidence = float(next_issue.get("confidence") or 0.0)
                next_issue["confidence"] = round(min(0.99, max(0.01, current_confidence + adjustment)), 2)
                confidence_breakdown = dict(next_issue.get("confidence_breakdown") or {})
                confidence_breakdown["review_learning_case"] = {
                    "action": "boost",
                    "matched_case_id": str(learning_decision.get("matched_case_id") or ""),
                    "similarity": float(learning_decision.get("similarity") or 0.0),
                    "confidence_adjustment": adjustment,
                    "reason": str(learning_decision.get("reason") or ""),
                }
                next_issue["confidence_breakdown"] = confidence_breakdown
                filtered_issues.append(next_issue)
                continue
            filtered_issues.append(issue)
        return filtered_issues, decisions

    def _prepare_review_workspace(self, review: ReviewTask, runtime_settings: object) -> ReviewTask:
        if not bool(getattr(runtime_settings, "enable_review_workspace_realtime_graph", False)):
            return self._skip_review_workspace_for_configured_repo_graph(review, runtime_settings)
        try:
            result = self.review_workspace_service.prepare(
                review_id=review.review_id,
                subject=review.subject,
                runtime=runtime_settings,  # type: ignore[arg-type]
            )
        except Exception as error:
            logger.exception("review workspace prepare failed review_id=%s", review.review_id)
            metadata = dict(review.subject.metadata or {})
            metadata["review_workspace"] = {
                "status": "failed",
                "message": f"MR 快照工作区准备失败：{error}",
                "error_type": error.__class__.__name__,
            }
            metadata["review_workspace_status"] = "failed"
            metadata["review_workspace_message"] = str(metadata["review_workspace"]["message"])
            review.subject.metadata = metadata
            self.review_repo.save(review)
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="review_workspace_failed",
                    phase="intake",
                    message="MR 代码快照准备失败，后续将使用原始仓库上下文。",
                    payload=metadata["review_workspace"],
                )
            )
            return review

        payload = result.model_dump()
        metadata = dict(review.subject.metadata or {})
        previous_workspace = str(metadata.get("workspace_repo_path") or "").strip()
        metadata["review_workspace"] = payload
        metadata["review_workspace_status"] = result.status
        metadata["review_workspace_message"] = result.message
        if result.status == "ready":
            metadata["base_workspace_repo_path"] = result.base_repo_path or previous_workspace
            metadata["workspace_repo_path"] = result.workspace_path
            metadata["repo_context_workspace_path"] = result.workspace_path
            metadata["review_workspace_path"] = result.workspace_path
            metadata["review_workspace_status"] = "ready"
            metadata["review_workspace_snapshot_mode"] = result.snapshot_mode
            metadata["review_workspace_commit"] = result.snapshot_commit
            metadata["review_workspace_diff_hash"] = result.diff_hash
            metadata["review_workspace_base_repo_path"] = result.base_repo_path
            self._append_review_workspace_graph_message(
                review.review_id,
                message_type="review_workspace_code_graph_started",
                content="代码结构图谱初始化开始，将基于本次 MR 合入快照解析 Java 符号和调用关系。",
                graph_name="代码结构图谱",
                graph_status="started",
                payload={"repo_path": result.workspace_path},
            )
            code_graph_result = self._build_review_workspace_code_graph(review.review_id, result)
            metadata["review_workspace_code_graph"] = code_graph_result
            self._append_review_workspace_graph_message(
                review.review_id,
                message_type="review_workspace_code_graph_completed",
                content=self._workspace_graph_completed_content("代码结构图谱", code_graph_result),
                graph_name="代码结构图谱",
                graph_status=str(code_graph_result.get("status") or ""),
                payload=code_graph_result,
            )
            prepared_subject = review.subject.model_copy(update={"metadata": metadata})
            self._append_review_workspace_graph_message(
                review.review_id,
                message_type="review_workspace_gitnexus_graph_started",
                content="GitNexus 快照图谱初始化开始，将在本次 MR 合入快照 worktree 上执行建图/确认图谱。",
                graph_name="GitNexus",
                graph_status="started",
                payload={"repo_path": result.workspace_path},
            )
            gitnexus_result = self._build_review_workspace_gitnexus_graph(prepared_subject, runtime_settings)
            metadata["review_workspace_gitnexus_graph"] = gitnexus_result
            self._append_review_workspace_graph_message(
                review.review_id,
                message_type="review_workspace_gitnexus_graph_completed",
                content=self._workspace_graph_completed_content("GitNexus", gitnexus_result),
                graph_name="GitNexus",
                graph_status=str(gitnexus_result.get("status") or ""),
                payload=gitnexus_result,
            )
        review.subject.metadata = metadata
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="review_workspace_prepared",
                phase="intake",
                message=(
                    "MR 合入快照已准备完成，GitNexus/代码结构图谱将读取快照代码。"
                    if result.status == "ready"
                    else f"MR 合入快照未启用：{result.message}"
                ),
                payload=payload,
            )
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="review_workspace",
                content=self._review_workspace_message_content(
                    result,
                    metadata.get("review_workspace_code_graph"),
                    metadata.get("review_workspace_gitnexus_graph"),
                ),
                metadata={"phase": "intake", **payload},
            )
        )
        return review

    def _skip_review_workspace_for_configured_repo_graph(self, review: ReviewTask, runtime_settings: object) -> ReviewTask:
        metadata = dict(review.subject.metadata or {})
        configured_repo_path = self._configured_repository_path(review.subject, runtime_settings)
        payload = {
            "status": "skipped",
            "message": "实时快照图谱开关关闭，未创建 MR worktree，后续使用设置页配置代码仓的已有图谱。",
            "workspace_path": "",
            "base_repo_path": configured_repo_path,
            "repository_id": str(metadata.get("repository_id") or review.subject.repo_id or ""),
            "review_id": review.review_id,
            "target_ref": review.subject.target_ref,
            "source_ref": review.subject.source_ref,
            "snapshot_mode": "configured_repo_graph",
        }
        metadata["review_workspace"] = payload
        metadata["review_workspace_status"] = "skipped"
        metadata["review_workspace_message"] = str(payload["message"])
        metadata["configured_workspace_repo_path"] = configured_repo_path
        incoming_workspace_repo_path = str(metadata.get("workspace_repo_path") or "").strip()
        has_explicit_review_workspace = bool(
            incoming_workspace_repo_path
            or str(metadata.get("repo_context_workspace_path") or "").strip()
            or str(metadata.get("code_graph_db_path") or "").strip()
            or isinstance(metadata.get("local_graph_build"), dict)
        )
        if configured_repo_path and not has_explicit_review_workspace:
            metadata["workspace_repo_path"] = configured_repo_path
            metadata["repo_context_workspace_path"] = configured_repo_path
        for key in (
            "review_workspace_path",
            "review_workspace_commit",
            "review_workspace_diff_hash",
            "review_workspace_code_graph",
            "review_workspace_gitnexus_graph",
        ):
            metadata.pop(key, None)
        review.subject.metadata = metadata
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="review_workspace_skipped",
                phase="intake",
                message=str(payload["message"]),
                payload=payload,
            )
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="review_workspace",
                content="\n".join(
                    [
                        "MR 合入快照未启用。",
                        "- 原因：实时快照图谱开关关闭。",
                        "- 动作：未创建 MR worktree。",
                        f"- 使用代码仓：{configured_repo_path or '设置页未配置本地代码仓'}",
                        "- 图谱来源：设置页配置代码仓的 .code-review-graph 与 .gitnexus。",
                    ]
                ),
                metadata={"phase": "intake", **payload},
            )
        )
        return review

    def _configured_repository_path(self, subject: ReviewSubject, runtime_settings: object) -> str:
        try:
            repository = self.repository_resolver.resolve(runtime_settings, subject)  # type: ignore[arg-type]
            local_path = str(getattr(repository, "local_path", "") or "").strip()
            if local_path:
                return local_path
        except Exception:
            logger.debug("failed to resolve configured repository path for review workspace skip", exc_info=True)
        return str(getattr(runtime_settings, "code_repo_local_path", "") or "").strip()

    def _append_review_workspace_graph_message(
        self,
        review_id: str,
        *,
        message_type: str,
        content: str,
        graph_name: str,
        graph_status: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        payload = dict(payload or {})
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type=message_type,
                content=content,
                metadata={
                    "phase": "intake",
                    "graph_name": graph_name,
                    "graph_status": graph_status,
                    **payload,
                },
            )
        )

    def _workspace_graph_completed_content(self, graph_name: str, graph_result: dict[str, object]) -> str:
        status = str(graph_result.get("status") or "unknown")
        message = str(graph_result.get("message") or "").strip()
        graph_path = str(graph_result.get("graph_db_path") or graph_result.get("graph_dir") or "").strip()
        title = "代码结构图谱初始化完成。" if graph_name == "代码结构图谱" else f"{graph_name} 快照图谱初始化完成。"
        lines = [title, f"- 状态：{status}"]
        if graph_path:
            lines.append(f"- 图谱路径：{graph_path}")
        indexed_file_count = graph_result.get("indexed_file_count")
        node_count = graph_result.get("node_count")
        edge_count = graph_result.get("edge_count")
        if any(isinstance(value, int) for value in (indexed_file_count, node_count, edge_count)):
            lines.append(
                "- 图谱规模："
                f"文件 {indexed_file_count if isinstance(indexed_file_count, int) else 0}，"
                f"节点 {node_count if isinstance(node_count, int) else 0}，"
                f"关系 {edge_count if isinstance(edge_count, int) else 0}"
            )
        if message:
            lines.append(f"- 说明：{message}")
        return "\n".join(lines)

    def _review_workspace_message_content(
        self,
        result,
        code_graph_result: object | None = None,
        gitnexus_result: object | None = None,
    ) -> str:
        if result.status == "ready":
            code_graph = dict(code_graph_result or {}) if isinstance(code_graph_result, dict) else {}
            code_graph_status = str(code_graph.get("status") or "skipped")
            graph_db_path = str(code_graph.get("graph_db_path") or "")
            gitnexus_graph = dict(gitnexus_result or {}) if isinstance(gitnexus_result, dict) else {}
            gitnexus_status = str(gitnexus_graph.get("status") or "skipped")
            gitnexus_graph_dir = str(gitnexus_graph.get("graph_dir") or "")
            lines = [
                "MR 合入快照已准备完成。",
                f"- 基础仓库：{result.base_repo_path}",
                f"- 快照路径：{result.workspace_path}",
                f"- 快照方式：{result.snapshot_mode}",
                f"- 快照 commit：{result.snapshot_commit[:12] if result.snapshot_commit else ''}",
                f"- 代码结构图谱：{code_graph_status}",
                f"- GitNexus 快照图谱：{gitnexus_status}",
            ]
            if graph_db_path:
                lines.append(f"- 代码结构图谱路径：{graph_db_path}")
            if gitnexus_graph_dir:
                lines.append(f"- GitNexus 图谱路径：{gitnexus_graph_dir}")
            lines.append(f"- 说明：{result.message}")
            return "\n".join(lines)
        return "\n".join(
            [
                "MR 合入快照未启用。",
                f"- 基础仓库：{result.base_repo_path}",
                f"- 目标分支：{result.target_ref}",
                f"- 源分支：{result.source_ref}",
                f"- 状态：{result.status}",
                f"- 说明：{result.message}",
            ]
        )

    def _build_review_workspace_code_graph(self, review_id: str, result) -> dict[str, object]:
        if result.status != "ready" or not result.workspace_path:
            return {"status": "skipped", "message": "MR 快照未就绪，跳过代码结构图谱建图。"}
        try:
            index_result = CodeGraphIndexService(
                repo_root=result.workspace_path,
                parser=JavaTreeSitterParser(),
            ).full_build(repository_id=f"{result.repository_id}__{review_id}", languages=["java"])
            return {"status": "ready", **index_result}
        except Exception as error:
            logger.exception("review workspace code graph build failed review_id=%s workspace_path=%s", review_id, result.workspace_path)
            return {
                "status": "failed",
                "message": f"代码结构图谱建图失败：{error}",
                "error_type": error.__class__.__name__,
                "repo_path": result.workspace_path,
            }

    def _build_review_workspace_gitnexus_graph(self, subject: ReviewSubject, runtime_settings: object) -> dict[str, object]:
        try:
            repository = self.repository_resolver.resolve(runtime_settings, subject)  # type: ignore[arg-type]
            if not bool(getattr(repository, "gitnexus_enabled", True)):
                return {"status": "skipped", "message": "当前仓库未启用 GitNexus，跳过 worktree 建图。"}
            return dict(self.gitnexus_impact_service.ensure_review_workspace_index(subject, runtime_settings))  # type: ignore[arg-type]
        except Exception as error:
            logger.exception("review workspace gitnexus graph build failed review_id=%s", subject.repo_id)
            return {
                "status": "failed",
                "message": f"GitNexus worktree 建图失败：{error}",
                "error_type": error.__class__.__name__,
            }

    def _attach_impact_report(self, review: ReviewTask, runtime_settings) -> ReviewTask:
        """在任务结果里持久化每个 MR 的关联影响报告。"""
        metadata = dict(review.subject.metadata or {})
        cached = metadata.get("impact_report") or metadata.get("gitnexus_impact_report")
        if not isinstance(cached, dict) or not cached:
            return review
        metadata["impact_report"] = dict(cached)
        return review.model_copy(update={"subject": review.subject.model_copy(update={"metadata": metadata})})

    def _run_change_impact_analysis_flow(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        runtime_settings,
    ) -> ReviewTask:
        latest = self._merge_review_metadata(
            review,
            {
                "impact_analysis_progress": {
                    "state": "started",
                    "expert_id": expert.expert_id,
                    "expert_name": expert.name_zh,
                    "started_at": datetime.now(UTC).isoformat(),
                }
            },
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=latest.review_id,
                issue_id="impact_report",
                expert_id=expert.expert_id,
                message_type="impact_analysis_started",
                content="关联性影响分析已启动，系统将调用 GitNexus 生成本次改动的影响范围和测试范围报告。",
                metadata={
                    "phase": "impact_analysis",
                    "expert_id": expert.expert_id,
                    "tool_name": "gitnexus_impact_analysis",
                    **self._expert_llm_metadata(expert, runtime_settings),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=latest.review_id,
                event_type="impact_analysis_started",
                phase="impact_analysis",
                message="关联性影响分析已启动",
                payload={"expert_id": expert.expert_id, "tool_name": "gitnexus_impact_analysis"},
            )
        )
        llm_result = None
        try:
            impact_report, impact_trace = self.gitnexus_impact_service.analyze_with_trace(latest.subject, runtime_settings)
            impact_trace = dict(impact_trace or {})
            impact_trace.setdefault("source_branch", str(latest.subject.source_ref or "").strip())
            impact_trace.setdefault("target_branch", str(latest.subject.target_ref or "").strip())
            impact_report, llm_result = self.change_impact_report_service.synthesize(
                expert=expert,
                runtime_settings=runtime_settings,
                report=impact_report,
                trace=impact_trace,
                review_id=latest.review_id,
            )
        except Exception as error:
            metadata = dict(latest.subject.metadata or {})
            metadata.pop("impact_report", None)
            metadata["impact_analysis_progress"] = {
                "state": "failed",
                "expert_id": expert.expert_id,
                "expert_name": expert.name_zh,
                "failed_at": datetime.now(UTC).isoformat(),
                "graph_status": "failed",
                "error_message": str(error),
            }
            updated = latest.model_copy(update={"subject": latest.subject.model_copy(update={"metadata": metadata})})
            updated.updated_at = datetime.now(UTC)
            self.review_repo.save(updated)
            self.message_repo.append(
                ConversationMessage(
                    review_id=updated.review_id,
                    issue_id="impact_report",
                    expert_id=expert.expert_id,
                    message_type="impact_report_failed",
                    content=f"关联影响分析失败：{error}",
                    metadata={
                        "phase": "impact_analysis",
                        "expert_id": expert.expert_id,
                        "tool_name": "gitnexus_impact_analysis",
                        "error_message": str(error),
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=updated.review_id,
                    event_type="impact_report_failed",
                    phase="impact_analysis",
                    message="关联影响报告生成失败",
                    payload={"expert_id": expert.expert_id, "error_message": str(error), "graph_status": "failed"},
                )
            )
            return updated

        metadata = dict(latest.subject.metadata or {})
        metadata["impact_report"] = impact_report.model_dump(mode="json")
        metadata["impact_analysis_progress"] = {
            "state": "completed",
            "expert_id": expert.expert_id,
            "expert_name": expert.name_zh,
            "completed_at": datetime.now(UTC).isoformat(),
            "graph_status": impact_report.graph_status,
            "risk_level": impact_report.risk_level,
            "changed_file_count": len(impact_report.changed_files),
            "impacted_file_count": len(impact_report.impacted_files),
            "recommended_test_scope_count": len(impact_report.recommended_test_scope),
        }
        updated = latest.model_copy(update={"subject": latest.subject.model_copy(update={"metadata": metadata})})
        updated.updated_at = datetime.now(UTC)
        self.review_repo.save(updated)
        self.message_repo.append(
            ConversationMessage(
                review_id=updated.review_id,
                issue_id="impact_report",
                expert_id=expert.expert_id,
                message_type="impact_report_generated",
                content=impact_report.report_summary
                or (
                    f"关联影响分析已完成：识别 {len(impact_report.changed_files)} 个变更文件，"
                    f"{len(impact_report.impacted_files)} 个候选受影响文件，"
                    f"给出 {len(impact_report.recommended_test_scope)} 条测试范围建议。"
                ),
                metadata={
                    "phase": "impact_analysis",
                    "expert_id": expert.expert_id,
                    "tool_name": "gitnexus_impact_analysis",
                    "impact_report": impact_report.model_dump(mode="json"),
                    **(self._llm_message_metadata(llm_result) if llm_result is not None else self._expert_llm_metadata(expert, runtime_settings)),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=updated.review_id,
                event_type="impact_report_generated",
                phase="impact_analysis",
                message="关联影响报告已生成",
                payload={
                    "expert_id": expert.expert_id,
                    "graph_status": impact_report.graph_status,
                    "risk_level": impact_report.risk_level,
                    "changed_file_count": len(impact_report.changed_files),
                    "impacted_file_count": len(impact_report.impacted_files),
                    "recommended_test_scope_count": len(impact_report.recommended_test_scope),
                },
            )
        )
        return updated

    def _augment_repository_context_with_quality_signals(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
        target_hunk: dict[str, object],
    ) -> dict[str, object]:
        enriched = dict(repository_context or {})
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=enriched,
            full_diff=self._build_target_file_full_diff(subject, file_path),
        )
        if list(java_quality.get("signals") or []):
            enriched["java_quality_signals"] = [
                str(item).strip()
                for item in list(java_quality.get("signals") or [])[:10]
                if str(item).strip()
            ]
        if str(java_quality.get("summary") or "").strip():
            enriched["java_quality_signal_summary"] = str(java_quality.get("summary") or "").strip()
        observations = self._normalize_review_observations(java_quality.get("observations"))
        if observations:
            enriched["review_observations"] = observations
        if dict(java_quality.get("analysis_stages") or {}):
            enriched["analysis_stages"] = dict(java_quality.get("analysis_stages") or {})
        primary_context = dict(enriched.get("primary_context") or {})
        if file_path and primary_context and not primary_context.get("path"):
            primary_context["path"] = file_path
            if line_start and not primary_context.get("line_start"):
                primary_context["line_start"] = line_start
            enriched["primary_context"] = primary_context
        return enriched

    def _build_code_graph_context_bundle(
        self,
        *,
        review: ReviewTask,
        runtime_settings: object,
        file_path: str,
        repository_context: dict[str, object],
    ) -> dict[str, object]:
        normalized_file_path = str(file_path or "").strip()
        if not normalized_file_path.lower().endswith(".java"):
            return {}
        try:
            repository = self.repository_resolver.resolve(runtime_settings, review.subject)  # type: ignore[arg-type]
            repository_id = repository.repository_id
        except Exception:
            logger.exception("failed to resolve repository for code graph context review_id=%s", review.review_id)
            repository_id = str(review.subject.repo_id or "").strip() or "default-repository"
        try:
            repository_service = self.repository_resolver.build_context_service(
                runtime_settings,  # type: ignore[arg-type]
                review.subject,
            )
        except Exception:
            logger.exception("failed to build repository context service for code graph context review_id=%s", review.review_id)
            repository_service = None
        planner = self.code_graph_context_planner
        graph_storage = self._build_code_graph_storage_for_repository(repository_service, review)
        if graph_storage is not None:
            planner = CodeGraphContextPlanner(code_graph_service=graph_storage)
        return planner.build_context_bundle(
            review_id=review.review_id,
            repository_id=repository_id,
            changed_files=[normalized_file_path],
            changed_symbols=self._derive_code_graph_changed_symbols(
                file_path=normalized_file_path,
                repository_context=repository_context,
            ),
            changed_ranges=self._code_graph_changed_ranges_for_file(review, normalized_file_path),
            repository_context_service=repository_service,
        )

    def _code_graph_changed_ranges_for_file(self, review: ReviewTask, file_path: str) -> dict[str, list[tuple[int, int]]]:
        ranges_by_file = self.diff_excerpt_service.changed_line_ranges_by_file(str(review.subject.unified_diff or ""))
        normalized_path = str(file_path or "").strip().replace("\\", "/")
        ranges = list(ranges_by_file.get(normalized_path) or [])
        return {normalized_path: ranges} if ranges else {}

    def _build_code_graph_storage_for_repository(
        self,
        repository_service: object | None,
        review: ReviewTask | None = None,
    ) -> CodeGraphStorage | None:
        metadata = dict(getattr(getattr(review, "subject", None), "metadata", None) or {})
        candidate_paths: list[Path] = []
        explicit_db_path = str(metadata.get("code_graph_db_path") or "").strip()
        if explicit_db_path:
            candidate_paths.append(Path(explicit_db_path).expanduser())
        workspace_repo_path = str(metadata.get("workspace_repo_path") or "").strip()
        if workspace_repo_path:
            candidate_paths.append(Path(workspace_repo_path).expanduser() / ".code-review-graph" / "graph.db")
        local_path = getattr(repository_service, "local_path", None)
        if local_path is not None:
            candidate_paths.append(Path(local_path).expanduser() / ".code-review-graph" / "graph.db")

        seen: set[str] = set()
        for graph_db_path in candidate_paths:
            normalized = str(graph_db_path)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if graph_db_path.exists():
                return CodeGraphStorage(graph_db_path)
        return None

    def _derive_code_graph_changed_symbols(
        self,
        *,
        file_path: str,
        repository_context: dict[str, object],
    ) -> list[str]:
        symbols: list[str] = []
        primary_context = dict(repository_context.get("primary_context") or {})
        for key in ("class_name", "method_name", "symbol", "name"):
            value = str(primary_context.get(key) or "").strip()
            if value:
                symbols.append(value)
        for item in list(repository_context.get("symbol_contexts") or []):
            if not isinstance(item, dict):
                continue
            value = str(item.get("symbol") or "").strip()
            if value:
                symbols.append(value)
        stem = Path(file_path).stem.strip()
        if stem:
            symbols.append(stem)
        result: list[str] = []
        for symbol in symbols:
            if symbol and symbol not in result:
                result.append(symbol)
        return result

    def _merge_code_graph_context_bundle(
        self,
        repository_context: dict[str, object],
        code_graph_bundle: dict[str, object],
    ) -> dict[str, object]:
        merged = dict(repository_context or {})
        related_contexts = [
            dict(item)
            for item in list(code_graph_bundle.get("related_contexts") or [])
            if isinstance(item, dict)
        ]
        if related_contexts:
            existing = [
                dict(item)
                for item in list(merged.get("code_graph_related_contexts") or [])
                if isinstance(item, dict)
            ]
            merged["code_graph_related_contexts"] = self._dedupe_context_dicts(existing + related_contexts)
            existing_related = [
                dict(item)
                for item in list(merged.get("related_contexts") or [])
                if isinstance(item, dict)
            ]
            merged["related_contexts"] = self._dedupe_context_dicts(existing_related + related_contexts)
        source_summary = dict(code_graph_bundle.get("source_summary") or {})
        if source_summary:
            merged["code_graph_context_source_summary"] = source_summary
        minimal_context = dict(code_graph_bundle.get("minimal_context") or {})
        if minimal_context:
            merged["code_graph_minimal_context"] = minimal_context
        impact_analysis = dict(code_graph_bundle.get("impact_analysis") or {})
        if impact_analysis:
            merged["code_graph_impact_analysis"] = impact_analysis
        return merged

    def _record_code_graph_context_bundle(self, review_id: str, code_graph_bundle: dict[str, object]) -> None:
        events = [
            event
            for event in list(code_graph_bundle.get("events") or [])
            if isinstance(event, ReviewEvent)
        ]
        for event in events:
            self.event_repo.append(event)
            self.message_repo.append(
                ConversationMessage(
                    review_id=review_id,
                    issue_id="review_orchestration",
                    expert_id=self.main_agent_service.agent_id,
                    message_type=event.event_type,
                    content=event.message,
                    metadata={
                        "phase": event.phase,
                        **dict(event.payload or {}),
                    },
                )
            )

    def _dedupe_context_dicts(self, contexts: list[dict[str, object]]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        seen: set[tuple[str, int, str]] = set()
        for item in contexts:
            path = str(item.get("path") or "").strip()
            try:
                line_number = int(item.get("line_number") or item.get("line_start") or 0)
            except (TypeError, ValueError):
                line_number = 0
            snippet = str(item.get("snippet") or "").strip()
            key = (path, line_number, snippet[:120])
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _enrich_issues_with_finding_evidence_chains(
        self,
        issues: list[DebateIssue],
        finding_payloads: list[dict[str, object]],
    ) -> None:
        if not issues or not finding_payloads:
            return
        by_id = {
            str(item.get("finding_id") or "").strip(): dict(item)
            for item in finding_payloads
            if str(item.get("finding_id") or "").strip()
        }
        for issue in issues:
            existing = [
                dict(item)
                for item in list(issue.evidence_chain or [])
                if isinstance(item, dict)
            ]
            graph_steps: list[dict[str, object]] = []
            context_sources: set[str] = set()
            for finding_id in list(issue.finding_ids or []):
                finding = by_id.get(str(finding_id))
                if not finding:
                    continue
                context_source = str(finding.get("context_source") or "").strip()
                if context_source:
                    context_sources.add(context_source)
                for step in list(finding.get("evidence_chain") or []):
                    if not isinstance(step, dict):
                        continue
                    if str(step.get("step") or "") in {
                        "minimal_context",
                        "changed_node",
                        "graph_relationship",
                        "review_priority",
                        "affected_flows",
                        "static_observation",
                        "sast_prescan",
                    }:
                        graph_steps.append(dict(step))
            issue.evidence_chain = self._dedupe_evidence_chain(existing + graph_steps)
            if any(source == "tree_sitter" for source in context_sources):
                breakdown = dict(issue.confidence_breakdown or {})
                breakdown["tree_sitter_context"] = True
                issue.confidence_breakdown = breakdown
                issue.tool_name = issue.tool_name or "tree_sitter_code_graph"
                issue.tool_verified = issue.tool_verified or bool(graph_steps)

    def _dedupe_evidence_chain(self, chain: list[dict[str, object]]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in chain:
            key = json.dumps(
                {
                    "step": item.get("step"),
                    "status": item.get("status"),
                    "file_path": item.get("file_path"),
                    "line_start": item.get("line_start"),
                    "summary": item.get("summary"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result[:20]

    def _append_deterministic_query_bound_findings(
        self,
        review: ReviewTask,
        finding_payloads: list[dict[str, object]],
    ) -> None:
        """把删除 LIMIT/分页边界这类确定性风险补成 finding，避免被 LLM 首轮遗漏。"""

        if not str(review.subject.unified_diff or "").strip():
            return
        existing = [
            item
            for item in finding_payloads
            if str(item.get("normalized_issue_type") or "").strip() in {"query_bound_removed", "query_boundary_missing"}
        ]
        if existing:
            return
        for file_path in review.subject.changed_files:
            normalized_file_path = str(file_path or "").strip()
            if not normalized_file_path.lower().endswith(".java"):
                continue
            for hunk in self.diff_excerpt_service.list_hunks(review.subject.unified_diff, normalized_file_path):
                excerpt = str(hunk.get("excerpt") or "")
                if not self._hunk_removes_query_bound(excerpt):
                    continue
                line_start = int(hunk.get("start_line") or 1)
                query_shape = self._query_shape_summary(excerpt)
                summary = "本次 diff 删除了查询的 LIMIT、分页或批量边界保护，数据量放大后可能返回大结果集并拖垮数据库访问路径。"
                if query_shape:
                    summary = f"{summary} 新查询形态包含 {query_shape}，应补回分页、LIMIT 或精确过滤边界。"
                finding = ReviewFinding(
                    review_id=review.review_id,
                    expert_id="database_analysis",
                    title="查询边界缺失",
                    summary=summary,
                    finding_type="direct_defect",
                    normalized_issue_type="query_bound_removed",
                    category_label="data_access",
                    severity="high",
                    confidence=0.92,
                    confidence_rationale="确定性规则信号；直接代码证据；命中 1 条规则；原始置信度 0.92",
                    file_path=normalized_file_path,
                    line_start=line_start,
                    evidence=self._query_bound_evidence(excerpt),
                    matched_rules=["PERF-SQL-001"],
                    violated_guidelines=["大结果集查询必须显式分页、LIMIT 或批量边界保护"],
                    rule_based_reasoning="diff 中直接出现 LIMIT/分页参数删除，且新增查询路径没有等价边界保护，属于可由静态 diff 确认的数据访问缺陷。",
                    remediation_strategy="恢复分页、LIMIT 或批量分片边界。",
                    remediation_suggestion="为该查询补回 LIMIT/分页约束，并确认批量消费只按固定窗口读取数据。",
                    remediation_steps=["恢复 LIMIT 或 setMaxResults", "保留批量参数绑定", "补充大数据量消费回归测试"],
                    code_excerpt=excerpt,
                    code_context={"deterministic_signal": "query_bound_removed"},
                    suggested_code="",
                    suggested_code_language="java",
                )
                self.finding_repo.save(review.review_id, finding)
                finding_payloads.append(finding.model_dump(mode="json"))
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="finding_created",
                        phase="expert_review",
                        message="数据库专家通过确定性规则补充了查询边界缺失 finding",
                        payload={
                            "finding_id": finding.finding_id,
                            "expert_id": finding.expert_id,
                            "file_path": finding.file_path,
                            "line_start": finding.line_start,
                            "deterministic_signal": "query_bound_removed",
                        },
                    )
                )
                return

    def _query_shape_summary(self, excerpt: str) -> str:
        lowered = str(excerpt or "").lower()
        terms: list[str] = []
        if "like" in lowered:
            terms.append("like")
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*?(?:Like|Containing|Contains|StartsWith|EndsWith)[A-Za-z0-9_]*)\s*\(", str(excerpt or "")):
            terms.append(match.group(1))
        if "pageable" not in lowered and "page<" not in lowered and "page" in lowered:
            terms.append("分页被移除")
        return " / ".join(self._merge_unique(terms)[:4])

    def _append_deterministic_java_quality_findings(
        self,
        review: ReviewTask,
        finding_payloads: list[dict[str, object]],
    ) -> None:
        """把 Java diff 中可直接确认的锁删除和循环调用放大补成 finding。"""

        if not str(review.subject.unified_diff or "").strip():
            return

        existing_keys = {
            (
                str(item.get("file_path") or "").strip(),
                str(item.get("normalized_issue_type") or "").strip(),
                int(self._normalize_optional_line_value(item.get("line_start")) or 1),
            )
            for item in finding_payloads
        }
        profiles = {
            "lock_guard_removed": {
                "expert_id": "performance_reliability",
                "title": "并发保护被删除",
                "normalized_issue_type": "lock_guard_removed",
                "category_label": "performance",
                "summary": "本次 diff 删除了 synchronized、Lock 或分布式锁等并发保护，新增代码没有看到等价保护。",
                "matched_rules": ["CONCURRENCY-LOCK-001"],
                "violated_guidelines": ["并发保护不能在没有等价替代机制时被删除"],
                "rule_based_reasoning": "删除行中出现锁保护，新增行没有同步、锁或幂等替代，属于可由静态 diff 直接确认的并发风险。",
                "remediation_strategy": "恢复原有锁保护，或补充数据库唯一约束、乐观锁、幂等表、分布式锁等等价并发控制。",
                "remediation_suggestion": "不要直接删除并发保护；先说明替代机制，并补充并发提交或重复消费回归测试。",
                "remediation_steps": ["恢复原有锁保护或补上等价并发控制", "补充重复提交和并发提交回归测试", "在代码评审说明中写明替代保护机制"],
                "confidence": 0.88,
            },
            "loop_call_amplification": {
                "expert_id": "performance_reliability",
                "title": "循环内逐条外部调用会放大批量处理成本",
                "normalized_issue_type": "n_plus_one",
                "category_label": "performance",
                "summary": "本次 diff 把仓储、服务、网关或客户端调用放进循环路径，批量输入会把外部依赖访问放大为 N 次。",
                "matched_rules": ["PERF-LOOP-001"],
                "violated_guidelines": ["循环体内不应逐条执行仓储、远程调用或消息发送"],
                "rule_based_reasoning": "新增代码中循环体直接调用外部依赖，这类调用放大可由静态 diff 直接确认。",
                "remediation_strategy": "把循环内逐条外部调用改成批量查询、批量提交或循环外聚合后统一处理。",
                "remediation_suggestion": "优先收集批量输入后调用批量接口，避免每个元素都触发一次外部依赖访问。",
                "remediation_steps": ["把循环内逐条调用移到循环外统一处理", "改用批量查询、批量保存或固定窗口批处理", "补充大批量输入场景回归测试"],
                "confidence": 0.86,
            },
            "comment_contract_unimplemented": {
                "expert_id": "correctness_business",
                "title": "承诺未落地",
                "normalized_issue_type": "comment_contract_unimplemented",
                "category_label": "correctness",
                "summary": "本次 diff 新增或保留了 TODO/注释承诺，但当前实现没有对应业务动作，调用方会误以为该能力已经落地。",
                "matched_rules": ["CORRECTNESS-CONTRACT-001"],
                "violated_guidelines": ["注释、TODO、接口说明和方法意图必须与真实实现保持一致"],
                "rule_based_reasoning": "新增代码中存在 TODO 或注释承诺，且当前 hunk 未出现承诺动作的实现，属于可由静态 diff 直接确认的语义缺口。",
                "remediation_strategy": "补齐注释承诺的业务动作；如果短期不实现，应删除误导性承诺并改成明确的待办跟踪。",
                "remediation_suggestion": "不要只留下 TODO。要么实现库存扣减、预占事件等承诺动作，要么删除该承诺并把未完成项移到任务系统。",
                "remediation_steps": ["补齐 TODO 或注释中承诺的业务动作", "如果本轮不交付该动作，删除误导性承诺并拆出任务", "增加覆盖该业务动作的回归测试"],
                "confidence": 0.84,
            },
            "exception_swallowed": {
                "expert_id": "correctness_business",
                "title": "失败被当成成功返回",
                "normalized_issue_type": "exception_swallowed",
                "category_label": "correctness",
                "summary": "本次 diff 的 catch 分支吞掉 RuntimeException，并在失败路径返回成功结果，调用方会误以为处理已经完成。",
                "matched_rules": ["CODE-JAVA-002", "REL-JDDD-001"],
                "violated_guidelines": ["关键链路 catch 分支不得忽略异常或把失败伪装成成功"],
                "rule_based_reasoning": "新增代码中 catch 分支包含 ignored/返回 success 等强锚点，异常路径和成功返回直接冲突。",
                "remediation_strategy": "恢复失败语义：记录必要上下文，并重新抛出异常、返回失败结果或触发补偿，不能在失败路径返回成功。",
                "remediation_suggestion": "把 catch 中的 success 返回改为抛出或失败结果，并补充支付网关异常的回归测试。",
                "remediation_steps": ["保留异常对象和错误上下文", "不要在 catch 分支返回 success", "补充网关失败和仓储保存失败的测试"],
                "confidence": 0.9,
            },
            "exception_semantics_weakened": {
                "expert_id": "correctness_business",
                "title": "失败被包装成成功返回",
                "normalized_issue_type": "exception_swallowed",
                "category_label": "correctness",
                "summary": "本次 diff 的 catch 分支把失败路径包装成成功语义返回，调用方会误以为处理已经完成。",
                "matched_rules": ["CODE-JAVA-002", "REL-JDDD-001"],
                "violated_guidelines": ["关键链路 catch 分支不得把失败伪装成成功"],
                "rule_based_reasoning": "新增代码中 catch 分支包含 success 返回等强锚点，异常路径和成功返回直接冲突。",
                "remediation_strategy": "恢复失败语义：记录必要上下文，并重新抛出异常、返回失败结果或触发补偿，不能在失败路径返回成功。",
                "remediation_suggestion": "把 catch 中的 success 返回改为抛出或失败结果，并补充异常路径的回归测试。",
                "remediation_steps": ["保留异常对象和错误上下文", "不要在 catch 分支返回 success", "补充异常路径测试"],
                "confidence": 0.88,
            },
        }

        for file_path in review.subject.changed_files:
            normalized_file_path = str(file_path or "").strip()
            if not normalized_file_path.lower().endswith(".java"):
                continue
            for hunk in self.diff_excerpt_service.list_hunks(review.subject.unified_diff, normalized_file_path):
                excerpt = str(hunk.get("excerpt") or "")
                java_quality = self.java_quality_signal_extractor.extract(
                    file_path=normalized_file_path,
                    target_hunk=hunk,
                    full_diff=review.subject.unified_diff,
                )
                signals = {str(item).strip() for item in list(java_quality.get("signals") or []) if str(item).strip()}
                signal_terms = dict(java_quality.get("signal_terms") or {})
                for signal_name in (
                    "lock_guard_removed",
                    "loop_call_amplification",
                    "comment_contract_unimplemented",
                    "exception_swallowed",
                    "exception_semantics_weakened",
                ):
                    if signal_name not in signals:
                        continue
                    profile = profiles[signal_name]
                    issue_type = str(profile["normalized_issue_type"])
                    evidence_terms = [str(item).strip() for item in list(signal_terms.get(signal_name) or []) if str(item).strip()]
                    line_start = self._deterministic_java_signal_line_start(
                        excerpt=excerpt,
                        signal_name=signal_name,
                        evidence_terms=evidence_terms,
                        fallback_line=int(hunk.get("start_line") or 1),
                    )
                    key = (normalized_file_path, issue_type, line_start)
                    if key in existing_keys:
                        continue
                    evidence = evidence_terms[:3] or [str(profile["summary"])]
                    summary = str(profile["summary"])
                    rule_based_reasoning = str(profile["rule_based_reasoning"])
                    remediation_suggestion = str(profile["remediation_suggestion"])
                    if signal_name == "loop_call_amplification" and evidence_terms:
                        loop_token = evidence_terms[0]
                        call_terms = evidence_terms[1:] or evidence_terms[:1]
                        call_text = "、".join(call_terms)
                        summary = (
                            f"本次 diff 在循环 `{loop_token}` 内调用 `{call_text}`，"
                            "批量输入会把外部依赖访问放大为 N 次。"
                        )
                        rule_based_reasoning = (
                            f"新增代码中循环体直接调用 {call_text}，这类调用放大可由静态 diff 直接确认。"
                        )
                        remediation_suggestion = (
                            f"优先把循环内的 {call_text} 改成批量接口或循环外统一提交，"
                            "避免每个元素都触发一次外部依赖访问。"
                        )
                    finding = ReviewFinding(
                        review_id=review.review_id,
                        expert_id=str(profile["expert_id"]),
                        title=str(profile["title"]),
                        summary=summary,
                        finding_type="direct_defect",
                        normalized_issue_type=issue_type,
                        category_label=str(profile["category_label"]),
                        severity="high",
                        confidence=float(profile["confidence"]),
                        confidence_rationale="确定性 Java 质量信号；已基于当前 diff 直接定位代码证据。",
                        file_path=normalized_file_path,
                        line_start=line_start,
                        evidence=evidence,
                        matched_rules=[str(item) for item in list(profile["matched_rules"])],
                        violated_guidelines=[str(item) for item in list(profile["violated_guidelines"])],
                        rule_based_reasoning=rule_based_reasoning,
                        remediation_strategy=str(profile["remediation_strategy"]),
                        remediation_suggestion=remediation_suggestion,
                        remediation_steps=[str(item) for item in list(profile["remediation_steps"])],
                        code_excerpt=excerpt,
                        code_context={"deterministic_signal": signal_name},
                        suggested_code="",
                        suggested_code_language="java",
                        verification_needed=False,
                    )
                    self.finding_repo.save(review.review_id, finding)
                    finding_payloads.append(finding.model_dump(mode="json"))
                    existing_keys.add(key)
                    self.event_repo.append(
                        ReviewEvent(
                            review_id=review.review_id,
                            event_type="finding_created",
                            phase="expert_review",
                            message=f"系统通过确定性 Java 质量信号补充了 {issue_type} finding",
                            payload={
                                "finding_id": finding.finding_id,
                                "expert_id": finding.expert_id,
                                "file_path": finding.file_path,
                                "line_start": finding.line_start,
                                "deterministic_signal": signal_name,
                            },
                        )
                    )

    def _deterministic_java_signal_line_start(
        self,
        *,
        excerpt: str,
        signal_name: str,
        evidence_terms: list[str],
        fallback_line: int,
    ) -> int:
        preferred_terms: list[str] = []
        if signal_name == "comment_contract_unimplemented":
            preferred_terms = [term for term in evidence_terms if "todo" in term.lower() or term.strip().startswith(("//", "/*", "*"))]
        elif signal_name == "loop_call_amplification":
            preferred_terms = [term for term in evidence_terms if "." in term or "save" in term.lower()]
            preferred_terms.extend(evidence_terms)
        elif signal_name == "lock_guard_removed":
            preferred_terms = [term for term in evidence_terms if "synchronized" in term.lower() or "lock" in term.lower()]
            preferred_terms.extend(evidence_terms)
        else:
            preferred_terms = list(evidence_terms)
        numbered_lines: list[tuple[int, str]] = []
        for raw_line in str(excerpt or "").splitlines():
            match = re.match(r"^\s*(\d+)\s*\|\s*(?:[+\-]\s*)?(.*)$", raw_line)
            if not match:
                continue
            numbered_lines.append((int(match.group(1)), match.group(2).strip()))
        for term in preferred_terms:
            normalized_term = re.sub(r"\s+", "", str(term or "").lower())
            if not normalized_term:
                continue
            for line_no, line_text in numbered_lines:
                normalized_line = re.sub(r"\s+", "", line_text.lower())
                if normalized_term in normalized_line or normalized_line in normalized_term:
                    return line_no
        return int(fallback_line or 1)

    def _append_empty_diff_fallback_finding(
        self,
        review: ReviewTask,
        expert_jobs: list[dict[str, object]],
        finding_payloads: list[dict[str, object]],
        *,
        manual_expert_selection: bool,
    ) -> None:
        """自动 MR 只有文件清单时保留低置信度风险，避免报告页完全空白。"""

        metadata = dict(review.subject.metadata or {})
        if manual_expert_selection or expert_jobs or finding_payloads:
            return
        if str(review.subject.unified_diff or "").strip():
            return
        changed_files = [str(path).strip() for path in list(review.subject.changed_files or []) if str(path).strip()]
        if not changed_files or not bool(metadata.get("allow_empty_diff_fallback")):
            return
        primary_file = changed_files[0]
        finding = ReviewFinding(
            review_id=review.review_id,
            expert_id=FALLBACK_EXPERT_ID,
            title="变更内容不足，需补充 diff 后复核",
            summary=(
                "本次审核只拿到了变更文件清单，缺少可定位的 diff hunk。"
                "系统先保留为待验证风险，提醒补充平台 diff 或本地工作区上下文后再做正式结论。"
            ),
            finding_type="risk_hypothesis",
            normalized_issue_type="empty_diff_review_input",
            category_label="risk_hypothesis",
            severity="low",
            confidence=0.31,
            confidence_rationale="待验证风险；审核输入缺少 unified_diff；仍需复核；原始置信度 0.31",
            file_path=primary_file,
            line_start=1,
            evidence=[
                f"变更文件：{primary_file}",
                "审核输入缺少 unified_diff，无法确认新增/删除代码行。",
            ],
            assumptions=["该 finding 仅用于保留审核输入不足的风险，不应升级为阻塞合并问题。"],
            context_files=changed_files[:8],
            remediation_strategy="补齐 MR diff 或接入本地仓库上下文后重新审核。",
            remediation_suggestion="重新拉取平台 diff，或配置 workspace_repo_path 让系统可以读取本地变更。",
            remediation_steps=["补充 unified_diff", "确认变更行号", "重新运行代码检视"],
        )
        self.finding_repo.save(review.review_id, finding)
        finding_payloads.append(finding.model_dump(mode="json"))
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="finding_created",
                phase="expert_review",
                message="系统因缺少 diff 保留了一条低置信度待验证 finding",
                payload={
                    "finding_id": finding.finding_id,
                    "expert_id": finding.expert_id,
                    "file_path": finding.file_path,
                    "line_start": finding.line_start,
                    "deterministic_signal": "empty_diff_review_input",
                },
            )
        )

    def _append_deterministic_observation_findings(
        self,
        review: ReviewTask,
        expert_jobs: list[dict[str, object]],
        finding_payloads: list[dict[str, object]],
    ) -> None:
        """把高确定性的 observation 补成 finding，避免专家超时或漏报后结果变薄。"""

        observations = self._collect_observations_from_expert_jobs(expert_jobs)
        if not observations:
            return

        profiles = {
            "control_flow_with_external_call": {
                "expert_id": "performance_reliability",
                "title": "循环调用放大",
                "normalized_issue_type": "loop_call_amplification",
                "summary": "当前改动把仓储、远程接口或消息发送放进循环路径，批量场景下会线性放大数据库往返、网络调用和整体时延。",
                "matched_rules": ["PERF-LOOP-001"],
                "violated_guidelines": ["循环体内不应逐条执行仓储、远程调用或消息发送"],
                "rule_based_reasoning": "observation 已明确命中循环体中的外部依赖调用，这类问题可直接从代码结构确认，不需要依赖更多运行时条件。",
                "remediation_strategy": "把循环内逐条外部调用改成批量查询、批量提交或循环外聚合后统一处理。",
                "remediation_suggestion": "优先把循环内仓储/远程调用提到循环外，避免每个元素都触发一次外部依赖访问。",
                "remediation_steps": ["把循环内逐条调用移到循环外统一处理", "改用批量查询、批量保存或固定窗口批处理", "补充大批量输入场景回归测试"],
                "suggested_code": "",
                "confidence_min": 0.65,
                "confidence_cap": 0.78,
            },
            "lock_guard_removed": {
                "expert_id": "performance_reliability",
                "title": "并发保护被删除",
                "normalized_issue_type": "lock_guard_removed",
                "summary": "当前改动删除了 synchronized、Lock 或分布式锁等并发保护，重复提交或并发执行时可能出现状态错乱。",
                "matched_rules": ["CONCURRENCY-LOCK-001"],
                "violated_guidelines": ["涉及共享状态、库存、余额、幂等或批处理入口的并发保护不能被无替代地删除"],
                "rule_based_reasoning": "observation 已明确命中锁保护从 diff 中被删除，且新增代码没有等价并发控制，这类竞态风险可以从代码结构直接确认。",
                "remediation_strategy": "恢复原有锁保护，或用数据库唯一约束、乐观锁、幂等表、分布式锁等等价机制替代。",
                "remediation_suggestion": "不要直接删除并发保护；先说明替代机制，再补充并发提交或重复消费场景的回归测试。",
                "remediation_steps": ["恢复原有锁保护或补上等价并发控制", "补充重复提交和并发提交回归测试", "在代码评审说明中写明替代保护机制"],
                "suggested_code": "",
                "confidence_min": 0.68,
                "confidence_cap": 0.82,
            },
            "declared_intent_without_implementation": {
                "expert_id": "correctness_business",
                "title": "承诺未落地",
                "normalized_issue_type": "comment_contract_unimplemented",
                "summary": "注释、TODO 或方法意图已经承诺了行为，但当前实现没有对应动作，调用方会误以为能力已经落地。",
                "matched_rules": ["CORRECTNESS-CONTRACT-001"],
                "violated_guidelines": ["注释、TODO、接口说明和方法意图必须与真实实现保持一致"],
                "rule_based_reasoning": "observation 已明确命中注释或待办承诺与实现不一致，这类语义缺口可以直接从 diff 和上下文判断。",
                "remediation_strategy": "要么补齐承诺中的行为，要么删除会误导调用方的注释、TODO 或命名表达。",
                "remediation_suggestion": "如果该业务动作属于本次交付范围，就补齐实现；如果不属于本次交付范围，就删除失效承诺并同步修正文档或命名。",
                "remediation_steps": ["补齐注释或 TODO 承诺的业务动作", "删除不属于本次交付范围的失效承诺", "同步修正注释、TODO 或接口说明"],
                "suggested_code": "",
                "confidence_min": 0.65,
                "confidence_cap": 0.78,
            },
            "error_handling_weakened": {
                "expert_id": "correctness_business",
                "title": "失败被忽略后仍继续处理",
                "normalized_issue_type": "exception_swallowed",
                "summary": "当前改动删除或削弱了 catch 分支里的异常处理，失败路径会被忽略，调用方和运维侧难以及时发现真实错误。",
                "matched_rules": ["CODE-JAVA-002"],
                "violated_guidelines": ["catch 分支不能忽略异常，至少需要日志、重新抛出或明确补偿处理"],
                "rule_based_reasoning": "observation 已明确命中 catch 分支为空或原有异常处理被删除，这类错误处理退化可以直接从 diff 和当前代码结构确认。",
                "remediation_strategy": "恢复异常处理语义，至少记录错误上下文；若该异常应阻断流程，则重新抛出业务异常或让事务回滚。",
                "remediation_suggestion": "不要保留空 catch。结合当前组件语义选择 logger.error、重新抛出或补偿处理，并补充异常分支回归测试。",
                "remediation_steps": ["恢复 catch 分支中的错误处理", "补充包含事件名/聚合 ID 的错误上下文", "增加异常分支测试覆盖"],
                "suggested_code": "catch (Exception e) {\n    logger.error(\"Failed to consume domain event\", e);\n    throw e;\n}",
                "confidence_min": 0.68,
                "confidence_cap": 0.8,
            },
        }

        for observation in observations:
            kind = str(observation.get("kind") or "").strip()
            profile = profiles.get(kind)
            if not profile:
                continue
            if kind == "declared_intent_without_implementation" and self._observation_has_implemented_interface_contract(observation):
                continue
            file_path = str(observation.get("file_path") or "").strip()
            line_start = int(self._normalize_optional_line_value(observation.get("line_start")) or 1)
            normalized_issue_type = str(profile.get("normalized_issue_type") or "").strip()
            if self._has_matching_deterministic_finding(
                finding_payloads,
                file_path=file_path,
                line_start=line_start,
                normalized_issue_type=normalized_issue_type,
                title=str(profile.get("title") or ""),
            ):
                continue
            evidence = [str(item).strip() for item in list(observation.get("evidence") or []) if str(item).strip()]
            related_symbols = [
                str(item).strip() for item in list(observation.get("related_symbols") or []) if str(item).strip()
            ]
            symbol_display = " / ".join(related_symbols[:2]) if related_symbols else "当前代码路径"
            claim = str(observation.get("summary") or "").strip()
            if kind == "control_flow_with_external_call":
                claim = (
                    f"当前实现把外部依赖调用放进循环路径（{symbol_display}），"
                    "批量场景下会线性放大数据库/网络往返与整体时延。"
                )
            elif kind == "lock_guard_removed":
                claim = (
                    f"当前改动删除了锁或并发保护（{symbol_display}），"
                    "但没有看到等价替代机制，重复提交或并发执行时可能破坏状态一致性。"
                )
            elif kind == "declared_intent_without_implementation":
                claim = (
                    f"注释、TODO 或方法意图已经承诺了行为（{symbol_display}），"
                    "但当前实现没有对应动作，属于直接的语义缺口。"
                )
            elif kind == "error_handling_weakened":
                claim = (
                    f"当前 catch 分支的异常处理被删除或变成空实现（{symbol_display}），"
                    "失败路径会被忽略，导致事件消费、补偿或排障链路失去错误信号。"
                )
            finding = ReviewFinding(
                review_id=review.review_id,
                expert_id=str(profile["expert_id"]),
                title=str(profile["title"]),
                summary=str(profile["summary"]),
                finding_type="risk_hypothesis",
                normalized_issue_type=normalized_issue_type,
                category_label=self._category_label_for_finding(
                    finding_type="risk_hypothesis",
                    issue_type=normalized_issue_type,
                    expert_id=str(profile["expert_id"]),
                ),
                severity="high",
                confidence=min(
                    max(float(observation.get("confidence") or 0.0), float(profile.get("confidence_min") or 0.65)),
                    float(profile.get("confidence_cap") or 0.78),
                ),
                confidence_rationale="结构化观察信号；需要补充上下文或二次验证后再升级为确定缺陷",
                file_path=file_path,
                line_start=line_start,
                evidence=evidence[:3] or [claim],
                matched_rules=[str(item).strip() for item in list(profile.get("matched_rules") or []) if str(item).strip()],
                violated_guidelines=[
                    str(item).strip() for item in list(profile.get("violated_guidelines") or []) if str(item).strip()
                ],
                rule_based_reasoning=str(profile["rule_based_reasoning"]),
                remediation_strategy=str(profile["remediation_strategy"]),
                remediation_suggestion=str(profile["remediation_suggestion"]),
                remediation_steps=[str(item).strip() for item in list(profile.get("remediation_steps") or []) if str(item).strip()],
                code_excerpt="\n".join(evidence[:3]),
                code_context={
                    "deterministic_signal": normalized_issue_type,
                    "observation_id": str(observation.get("observation_id") or "").strip(),
                    "observation_kind": kind,
                    "verification_profile": "observation_signal_requires_review",
                },
                suggested_code=str(profile["suggested_code"]),
                suggested_code_language="java",
                verification_needed=True,
                verification_plan="该 finding 由结构化观察信号补充生成，需要结合完整上下文、调用规模或业务契约进行二次验证。",
            )
            self.finding_repo.save(review.review_id, finding)
            finding_payloads.append(finding.model_dump(mode="json"))
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="finding_created",
                    phase="expert_review",
                    message=f"{finding.expert_id} 通过 observation 兜底补充了 {finding.title} finding",
                    payload={
                        "finding_id": finding.finding_id,
                        "expert_id": finding.expert_id,
                        "file_path": finding.file_path,
                        "line_start": finding.line_start,
                        "deterministic_signal": normalized_issue_type,
                    },
                )
            )

    def _collect_observations_from_expert_jobs(self, expert_jobs: list[dict[str, object]]) -> list[dict[str, object]]:
        collected: list[dict[str, object]] = []
        seen: set[tuple[str, str, int, str]] = set()
        for job in expert_jobs:
            if not isinstance(job, dict):
                continue
            collected.extend(self._dedupe_observation_items(self._normalize_review_observations(dict(job.get("repository_context") or {}).get("review_observations")), seen))
            batch_items = [dict(item) for item in list(job.get("batch_items") or []) if isinstance(item, dict)]
            for item in batch_items:
                repository_context = dict(item.get("repository_context") or {})
                collected.extend(
                    self._dedupe_observation_items(
                        self._normalize_review_observations(repository_context.get("review_observations")),
                        seen,
                    )
                )
        return collected

    def _dedupe_observation_items(
        self,
        observations: list[dict[str, object]],
        seen: set[tuple[str, str, int, str]],
    ) -> list[dict[str, object]]:
        deduped: list[dict[str, object]] = []
        for item in observations:
            file_key = str(item.get("file_path") or "").strip().lower()
            kind_key = str(item.get("kind") or "").strip().lower()
            line_key = int(self._normalize_optional_line_value(item.get("line_start")) or 1)
            signal_key = str(item.get("signal") or "").strip().lower()
            key = (file_key, kind_key, line_key, signal_key)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(dict(item))
        return deduped

    def _has_matching_deterministic_finding(
        self,
        finding_payloads: list[dict[str, object]],
        *,
        file_path: str,
        line_start: int,
        normalized_issue_type: str,
        title: str,
    ) -> bool:
        file_key = str(file_path or "").strip().lower()
        title_key = str(title or "").strip().lower()
        issue_type_key = str(normalized_issue_type or "").strip().lower()
        for item in finding_payloads:
            existing_file = str(item.get("file_path") or "").strip().lower()
            if existing_file != file_key:
                continue
            existing_line = int(self._normalize_optional_line_value(item.get("line_start")) or 1)
            if abs(existing_line - int(line_start or 1)) > 2:
                continue
            existing_type = str(item.get("normalized_issue_type") or "").strip().lower()
            existing_title = str(item.get("title") or "").strip().lower()
            if issue_type_key and existing_type == issue_type_key:
                return True
            if title_key and existing_title == title_key:
                return True
        return False

    @staticmethod
    def _hunk_removes_query_bound(excerpt: str) -> bool:
        removed_lines = [
            line.lower()
            for line in str(excerpt or "").splitlines()
            if "   - |" in line
        ]
        added_lines = [
            line.lower()
            for line in str(excerpt or "").splitlines()
            if re.search(r"\|\s*\+", line)
        ]
        removed_blob = "\n".join(removed_lines)
        added_blob = "\n".join(added_lines)
        removed_bound = any(
            token in removed_blob
            for token in (" limit ", " limit :", "setmaxresults", "pageable", "pagerequest", "setparameter(\"chunk\"")
        )
        added_equivalent_bound = any(
            token in added_blob
            for token in (" limit ", "setmaxresults", "pageable", "pagerequest")
        )
        return removed_bound and not added_equivalent_bound

    @staticmethod
    def _query_bound_evidence(excerpt: str) -> list[str]:
        evidence = [
            line.strip()
            for line in str(excerpt or "").splitlines()
            if "   - |" in line and any(token in line.lower() for token in ("limit", "setmaxresults", "page", "chunk"))
        ]
        added_query = [
            line.strip()
            for line in str(excerpt or "").splitlines()
            if re.search(r"\|\s*\+", line) and any(token in line.lower() for token in ("select", "query", "list()"))
        ]
        return (evidence + added_query)[:4] or ["diff 显示查询边界保护被删除。"]

    def clear_runtime_caches(self) -> None:
        """清理 ReviewRunner 持有的长生命周期缓存。"""

        self.main_agent_service.clear_runtime_caches()
        self.knowledge_service.clear_runtime_caches()

    def _record_expert_job_failure(
        self,
        job: dict[str, object],
        exc: Exception,
    ) -> dict[str, object]:
        review = job["review"]
        expert = job["expert"]
        file_path = str(job.get("file_path") or "")
        line_start = int(job.get("line_start") or 1)
        command_message = job["command_message"]
        assert isinstance(review, ReviewTask)
        assert isinstance(expert, ExpertProfile)
        assert isinstance(command_message, ConversationMessage)
        error_text = str(exc).strip() or exc.__class__.__name__
        payload = {
            "expert_id": expert.expert_id,
            "expert_name": expert.name_zh,
            "file_path": file_path,
            "line_start": line_start,
            "error_type": exc.__class__.__name__,
            "error": error_text,
        }
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_failed",
                content=f"{expert.name_zh} 执行失败：{error_text}",
                metadata={
                    "phase": "expert_review",
                    "reply_to_message_id": command_message.message_id,
                    **payload,
                },
            )
            )
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="expert_failed",
                phase="expert_review",
                message=f"{expert.name_zh} 执行失败，系统将继续保留其他专家结果。",
                payload=payload,
            )
        )

        logger.exception(
            "expert execution failed review_id=%s expert_id=%s file_path=%s line_start=%s error=%s",
            review.review_id,
            expert.expert_id,
            file_path,
            line_start,
            error_text,
        )
        fallback_finding = self._build_failed_expert_fallback_finding(job, error_text)
        if fallback_finding is not None:
            self.finding_repo.save(review.review_id, fallback_finding)
            finding_payloads = job.get("finding_payloads")
            if isinstance(finding_payloads, list):
                finding_payloads.append(fallback_finding.model_dump(mode="json"))
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id=fallback_finding.finding_id,
                    expert_id=expert.expert_id,
                    message_type="expert_analysis",
                    content="专家执行失败，系统已基于已命中规则、代码上下文和工具证据保守生成待验证风险。",
                    metadata={
                        "phase": "expert_review",
                        "severity": fallback_finding.severity,
                        "confidence": fallback_finding.confidence,
                        "file_path": fallback_finding.file_path,
                        "line_start": fallback_finding.line_start,
                        "finding_type": fallback_finding.finding_type,
                        "assumptions": fallback_finding.assumptions,
                        "matched_rules": fallback_finding.matched_rules,
                        "violated_guidelines": fallback_finding.violated_guidelines,
                        "rule_based_reasoning": fallback_finding.rule_based_reasoning,
                        "context_files": fallback_finding.context_files,
                        "input_completeness": fallback_finding.code_context.get("input_completeness", {}),
                        "review_inputs": fallback_finding.code_context.get("review_inputs", {}),
                        "fallback_generated": True,
                        "failure_reason": error_text,
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="finding_created",
                    phase="expert_review",
                    message=f"{expert.name_zh} 执行失败后保守生成待验证发现",
                    payload={"finding_id": fallback_finding.finding_id, "expert_id": expert.expert_id, "fallback_generated": True},
                )
            )
        return payload

    def _persist_deterministic_rule_signal_finding(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        command_message: ConversationMessage,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
        target_hunk: dict[str, object],
        target_hunks: list[dict[str, object]],
        bound_documents: list[object],
        rule_screening: dict[str, object],
        finding_payloads: list[dict[str, object]],
        reason: str,
    ) -> bool:
        finding = self._build_failed_expert_fallback_finding(
            {
                "review": review,
                "expert": expert,
                "command_message": command_message,
                "file_path": file_path,
                "line_start": line_start,
                "repository_context": repository_context,
                "target_hunk": target_hunk,
                "target_hunks": target_hunks,
                "bound_documents": bound_documents,
                "rule_screening": rule_screening,
            },
            reason,
        )
        if finding is None:
            return False
        if "执行失败后保守保留" in finding.title or "执行失败后" in finding.title:
            finding.title = f"{expert.name_zh} 规则预检命中：事件消费失败被忽略"
        if "专家执行失败" in finding.summary:
            finding.summary = (
                "结构化规则预检和静态上下文均显示，事件消费链路的 catch 块删除了异常处理语句，"
                "NoSuchMethodException 等反射异常会被忽略，后续处理无法感知消费失败。"
            )
        finding.evidence = [
            item.replace("专家执行失败:", "确定性规则证据:")
            for item in finding.evidence
        ]
        self.finding_repo.save(review.review_id, finding)
        finding_payloads.append(finding.model_dump(mode="json"))
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id=finding.finding_id,
                expert_id=expert.expert_id,
                message_type="expert_analysis",
                content="规则预检已命中，系统基于结构化规则、代码上下文和静态观察生成确定性 finding。",
                metadata={
                    "phase": "expert_review",
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "file_path": finding.file_path,
                    "line_start": finding.line_start,
                    "finding_type": finding.finding_type,
                    "normalized_issue_type": finding.normalized_issue_type,
                    "assumptions": finding.assumptions,
                    "matched_rules": finding.matched_rules,
                    "violated_guidelines": finding.violated_guidelines,
                    "rule_based_reasoning": finding.rule_based_reasoning,
                    "context_files": finding.context_files,
                    "input_completeness": finding.code_context.get("input_completeness", {}),
                    "review_inputs": finding.code_context.get("review_inputs", {}),
                    "deterministic_rule_signal_finding": True,
                    "deterministic_reason": reason,
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="finding_created",
                phase="expert_review",
                message=f"{expert.name_zh} 基于规则预检和静态证据生成确定性发现",
                payload={
                    "finding_id": finding.finding_id,
                    "expert_id": expert.expert_id,
                    "deterministic_rule_signal_finding": True,
                },
            )
        )
        return True

    def _should_short_circuit_rule_prepass_with_static_finding(
        self,
        *,
        rule_prepass_text: str,
        required_rule_ids: list[str],
        expert_id: str,
        file_path: str,
        target_hunk: dict[str, object],
        repository_context: dict[str, object],
    ) -> bool:
        required = {str(item).strip().upper() for item in required_rule_ids if str(item).strip()}
        if not required.intersection({"CORR-JDDD-002", "REL-JDDD-001"}):
            return False
        payload = self._parse_json_payload(rule_prepass_text)
        if not isinstance(payload, dict):
            return False
        rule_results = [dict(item) for item in list(payload.get("rule_check_results") or []) if isinstance(item, dict)]
        if not any(
            str(item.get("rule_id") or "").strip().upper() in required
            and self._normalize_rule_check_status(str(item.get("status") or "")).strip()
            in {"violated", "insufficient_context"}
            for item in rule_results
        ):
            return False
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=str(target_hunk.get("excerpt") or ""),
        )
        signals = {str(item).strip() for item in list(java_quality.get("signals") or []) if str(item).strip()}
        return expert_id in {"correctness_business", "performance_reliability"} and "exception_swallowed" in signals

    def _observation_has_implemented_interface_contract(self, observation: dict[str, object]) -> bool:
        """Avoid forcing comment-contract findings when implementation evidence already exists."""

        evidence_blob = "\n".join(
            str(item).strip()
            for item in [
                observation.get("summary"),
                *list(observation.get("evidence") or []),
                *list(observation.get("related_symbols") or []),
            ]
            if str(item).strip()
        )
        lowered = evidence_blob.lower()
        if "interface" not in lowered or "implements" not in lowered:
            return False
        interface_names = {
            match.group(1)
            for match in re.finditer(r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)\b", evidence_blob)
        }
        implemented_interfaces: set[str] = set()
        for match in re.finditer(r"\bimplements\s+([A-Za-z0-9_<>,\s.]+)", evidence_blob):
            implemented_interfaces.update(
                token.rsplit(".", 1)[-1]
                for token in re.split(r"[,<>\s]+", match.group(1))
                if token and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token)
            )
        if interface_names and implemented_interfaces and not (interface_names & implemented_interfaces):
            return False
        interface_methods = {
            match.group(1)
            for match in re.finditer(
                r"(?:public\s+)?(?:[\w.$<>\[\], ?]+\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*;",
                evidence_blob,
            )
        }
        implemented_methods = {
            match.group(1)
            for match in re.finditer(
                r"@Override\s+(?:public|protected|private)?\s*(?:[\w.$<>\[\], ?]+\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{",
                evidence_blob,
                flags=re.IGNORECASE,
            )
        }
        if interface_methods and implemented_methods and not (interface_methods & implemented_methods):
            return False
        if "@override" not in lowered and not implemented_methods:
            return False
        return bool(re.search(r"\{[^{}]*(?:;|\breturn\b|\bthrow\b|=|\.\s*[A-Za-z_][A-Za-z0-9_]*\s*\()", evidence_blob, flags=re.DOTALL))

    def _build_failed_expert_fallback_finding(
        self,
        job: dict[str, object],
        error_text: str,
    ) -> ReviewFinding | None:
        review = job.get("review")
        expert = job.get("expert")
        command_message = job.get("command_message")
        if not isinstance(review, ReviewTask) or not isinstance(expert, ExpertProfile) or not isinstance(command_message, ConversationMessage):
            return None
        rule_screening = dict(job.get("rule_screening") or {})
        matched_rules = [
            str(item.get("rule_id") or item.get("title") or "").strip()
            for item in list(rule_screening.get("matched_rules_for_llm") or [])[:4]
            if isinstance(item, dict) and str(item.get("rule_id") or item.get("title") or "").strip()
        ]
        if not matched_rules:
            return None
        file_path = str(job.get("file_path") or "")
        line_start = int(job.get("line_start") or 1)
        command_metadata = dict(getattr(command_message, "metadata", {}) or {})
        repository_context = dict(job.get("repository_context") or command_metadata.get("repository_context") or {})
        target_hunk = dict(job.get("target_hunk") or command_metadata.get("target_hunk") or {})
        forced_candidates = self._build_forced_observation_candidates(
            expert=expert,
            uncovered_observations=self._normalize_review_observations(repository_context.get("review_observations")),
            max_findings=1,
        )
        if forced_candidates:
            candidate = forced_candidates[0]
            forced_line_start = self._normalize_line_start(candidate.get("line_start"), line_start)
            evidence = [str(item).strip() for item in list(candidate.get("evidence") or []) if str(item).strip()]
            observation_ids = self._normalize_text_list(candidate.get("observation_ids"), [])
            finding = ReviewFinding(
                review_id=review.review_id,
                expert_id=expert.expert_id,
                title=str(candidate.get("title") or f"{expert.name_zh} 规则兜底发现"),
                summary=str(candidate.get("claim") or candidate.get("summary") or ""),
                finding_type=str(candidate.get("finding_type") or "risk_hypothesis"),
                severity=self._normalize_severity(candidate.get("severity"), "high"),
                confidence=self._normalize_confidence(candidate.get("confidence"), 0.74),
                file_path=str(candidate.get("file_path") or file_path),
                line_start=forced_line_start,
                evidence=evidence,
                cross_file_evidence=[str(item).strip() for item in list(candidate.get("cross_file_evidence") or []) if str(item).strip()],
                assumptions=[],
                context_files=self._merge_context_files(candidate.get("context_files", []), repository_context, []),
                matched_rules=self._normalize_text_list(candidate.get("matched_rules"), matched_rules),
                violated_guidelines=self._normalize_text_list(candidate.get("violated_guidelines"), matched_rules),
                rule_based_reasoning=str(candidate.get("rule_based_reasoning") or ""),
                verification_needed=bool(candidate.get("verification_needed", True)),
                verification_plan=str(candidate.get("verification_plan") or ""),
                remediation_strategy=str(candidate.get("fix_strategy") or self._build_remediation_strategy(review.subject, expert.expert_id, file_path)),
                remediation_suggestion=str(candidate.get("suggested_fix") or self._build_remediation_suggestion(review.subject, expert.expert_id, file_path)),
                remediation_steps=self._normalize_text_list(
                    candidate.get("change_steps"),
                    self._build_remediation_steps(review.subject, expert.expert_id, file_path),
                ),
                code_excerpt=self._build_code_excerpt(review.subject, file_path, forced_line_start, expert.expert_id),
                code_context=self._build_finding_code_context(
                    review.subject,
                    file_path,
                    forced_line_start,
                    target_hunk,
                    repository_context,
                    expert=expert,
                    bound_documents=list(job.get("bound_documents") or []),
                    rule_screening=rule_screening,
                ),
                suggested_code=str(candidate.get("suggested_code") or "").strip(),
                suggested_code_language=self._infer_code_language(file_path),
            )
            if observation_ids:
                code_context = dict(finding.code_context or {})
                code_context["observation_ids"] = observation_ids
                code_context["fallback_generated_from_observation"] = True
                code_context["failure_reason"] = error_text
                if candidate.get("evidence_source"):
                    code_context["evidence_source"] = str(candidate.get("evidence_source") or "")
                code_context["direct_evidence"] = bool(candidate.get("direct_evidence", False))
                finding.code_context = code_context
            return finding
        must_review_count = int(rule_screening.get("must_review_count") or 0)
        possible_hit_count = int(rule_screening.get("possible_hit_count") or 0)
        top_rule = next((item for item in list(rule_screening.get("matched_rules_for_llm") or []) if isinstance(item, dict)), {})
        rule_title = str(top_rule.get("title") or top_rule.get("rule_id") or matched_rules[0]).strip()
        rule_reason = str(top_rule.get("reason") or "").strip()
        confidence = 0.28 if must_review_count > 0 else 0.22 if possible_hit_count > 0 else 0.18
        summary = (
            f"专家执行失败，但基于已命中的规则“{rule_title}”和当前代码上下文，"
            f"此处仍存在待验证风险。{rule_reason or '建议优先按规则意图补充验证。'}"
        )
        fallback_payload = self._enrich_java_quality_signal_language(
            {
                "title": f"{expert.name_zh} 执行失败后保守保留的待验证风险",
                "summary": summary,
                "claim": summary,
                "evidence": [
                    f"专家执行失败: {error_text}",
                    f"规则命中: {rule_title}",
                    *( [rule_reason] if rule_reason else [] ),
                    *( [str(target_hunk.get("excerpt") or "").strip()] if str(target_hunk.get("excerpt") or "").strip() else [] ),
                ],
                "matched_rules": matched_rules,
                "violated_guidelines": matched_rules,
                "context_files": self._merge_context_files([], repository_context, []),
            },
            expert.expert_id,
            file_path,
            target_hunk,
            repository_context,
        )
        finding = ReviewFinding(
            review_id=review.review_id,
            expert_id=expert.expert_id,
            title=str(fallback_payload.get("title") or f"{expert.name_zh} 执行失败后保守保留的待验证风险"),
            summary=str(fallback_payload.get("summary") or summary),
            finding_type=str(fallback_payload.get("finding_type") or "risk_hypothesis"),
            severity=self._normalize_severity(fallback_payload.get("severity"), "medium"),
            confidence=self._normalize_confidence(fallback_payload.get("confidence"), confidence),
            normalized_issue_type=str(fallback_payload.get("normalized_issue_type") or "").strip(),
            file_path=file_path,
            line_start=line_start,
            evidence=[str(item).strip() for item in list(fallback_payload.get("evidence") or []) if str(item).strip()],
            assumptions=[
                "当前结论来自规则筛选、路由上下文与运行前证据，尚未得到完整专家 LLM 输出确认。"
            ],
            context_files=self._merge_context_files([], repository_context, []),
            matched_rules=matched_rules,
            violated_guidelines=matched_rules,
            rule_based_reasoning=rule_reason or f"命中规则 {rule_title}，需要补跑专家以确认具体违例证据。",
            verification_needed=bool(fallback_payload.get("verification_needed", True)),
            verification_plan=str(
                fallback_payload.get("verification_plan")
                or "系统会优先自动重试失败专家；若仍失败，再补齐关联源码与命中规则后自动复核是否升级为 issue。"
            ),
            remediation_strategy=self._build_remediation_strategy(review.subject, expert.expert_id, file_path),
            remediation_suggestion=self._build_remediation_suggestion(review.subject, expert.expert_id, file_path),
            remediation_steps=self._build_remediation_steps(review.subject, expert.expert_id, file_path),
            code_excerpt=self._build_code_excerpt(review.subject, file_path, line_start, expert.expert_id),
            code_context=self._build_finding_code_context(
                review.subject,
                file_path,
                line_start,
                target_hunk,
                repository_context,
                expert=expert,
                bound_documents=list(job.get("bound_documents") or []),
                rule_screening=rule_screening,
            ),
            suggested_code="",
            suggested_code_language=self._infer_code_language(file_path),
        )
        return finding

    def _abort_if_closed(self, review_id: str) -> None:
        """在关键阶段检查任务是否已被用户主动关闭。"""

        latest = self.review_repo.get(review_id)
        if latest is None:
            return
        metadata = dict(getattr(latest.subject, "metadata", {}) or {})
        if latest.status == "closed" or bool(metadata.get("close_requested")):
            raise ReviewClosedError(f"review {review_id} was closed by user")

    def _maybe_build_fallback_job(
        self,
        *,
        review: ReviewTask,
        enabled_experts: list[ExpertProfile],
        existing_jobs: list[dict[str, object]],
        selected_ids: list[str],
        skipped_experts: list[dict[str, object]],
        effective_runtime_settings,
        analysis_mode: Literal["standard", "light"],
        llm_request_options: dict[str, int | float],
        finding_payloads: list[dict[str, object]],
    ) -> dict[str, object] | None:
        """当用户选择的专家全部不匹配时，补入架构专家做兜底审查。"""
        metadata = dict(review.subject.metadata or {})
        allow_empty_diff_fallback = bool(metadata.get("allow_empty_diff_fallback"))
        if existing_jobs or not skipped_experts:
            return None
        if not review.subject.changed_files and not allow_empty_diff_fallback:
            return None
        fallback_expert = next((item for item in enabled_experts if item.expert_id == FALLBACK_EXPERT_ID), None)
        if fallback_expert is None or not fallback_expert.enabled:
            return None
        fallback_file = review.subject.changed_files[0] if review.subject.changed_files else self._pick_file_path(
            review.subject,
            fallback_expert.expert_id,
        )
        fallback_line = self.diff_excerpt_service.find_nearest_line(
            review.subject.unified_diff,
            fallback_file,
            1,
        ) or 1
        summary = (
            "**兜底派工指令**\n\n"
            f"**目标专家：** {fallback_expert.expert_id} / {fallback_expert.name_zh}\n\n"
            "用户选择的专家与当前变更相关性较低，系统已自动补入架构与设计专家执行保守型兜底审查。\n"
            f"请围绕 `{fallback_file}` 第 **{fallback_line} 行** 附近变更，优先检查结构性影响、接口契约、边界条件和明显测试缺口。\n"
            "若证据不足，请明确标记为待验证风险，不要越界输出数据库、安全、Redis 或 MQ 专项结论。"
        )
        command_message = self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="main_agent_command",
                content=summary,
                metadata={
                    "phase": "coordination",
                    "target_expert_id": fallback_expert.expert_id,
                    "target_expert_name": fallback_expert.name_zh,
                    "file_path": fallback_file,
                    "line_start": fallback_line,
                    "related_files": list(review.subject.changed_files[:4]),
                    "target_hunk": {},
                    "repository_context": {},
                    "expected_checks": [
                        "结构性影响",
                        "接口契约",
                        "边界条件",
                        "测试缺口",
                    ],
                    "disallowed_inference": [
                        "不要把 import 变化直接推断成架构问题",
                        "证据不足时只能输出待验证风险",
                    ],
                    "fallback_expert": True,
                    "fallback_reason": "selected_experts_mismatch",
                    "provider": "main-agent-template",
                    "model": "template",
                    "base_url": "",
                    "api_key_env": "",
                    "mode": "template",
                    "error": "",
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="fallback_expert_added",
                phase="coordination",
                message="系统已自动补入架构与设计专家作为兜底审查者",
                payload={
                    "expert_id": fallback_expert.expert_id,
                    "expert_name": fallback_expert.name_zh,
                    "selected_experts": selected_ids,
                    "skipped_experts": skipped_experts,
                    "file_path": fallback_file,
                    "line_start": fallback_line,
                },
            )
        )
        logger.info(
            "fallback expert added review_id=%s expert_id=%s skipped=%s",
            review.review_id,
            fallback_expert.expert_id,
            [item["expert_id"] for item in skipped_experts],
        )
        knowledge_context = self._build_knowledge_review_context(
            review.subject,
            fallback_expert,
            fallback_file,
            fallback_line,
            {},
            {},
        )
        bound_documents = self.knowledge_service.retrieve_for_expert(
            fallback_expert.expert_id,
            knowledge_context,
        )
        rule_screening = self.knowledge_service.screen_rules_for_expert(
            fallback_expert.expert_id,
            knowledge_context,
            runtime_settings=effective_runtime_settings,
            analysis_mode=analysis_mode,
            review_id=review.review_id,
        )
        logger.info(
            "fallback expert rule screening prepared review_id=%s expert_id=%s file_path=%s line_start=%s total_rules=%s matched_rule_count=%s must_review=%s possible_hit=%s matched_rule_ids=%s",
            review.review_id,
            fallback_expert.expert_id,
            fallback_file,
            fallback_line,
            int(rule_screening.get("total_rules") or 0),
            int(rule_screening.get("matched_rule_count") or 0),
            int(rule_screening.get("must_review_count") or 0),
            int(rule_screening.get("possible_hit_count") or 0),
            [
                str(item.get("rule_id") or "").strip()
                for item in list(rule_screening.get("matched_rules_for_llm", []) or [])[:8]
            ],
        )
        return {
            "review": review,
            "expert": fallback_expert,
            "command_message": command_message,
            "file_path": fallback_file,
            "line_start": fallback_line,
            "runtime_settings": effective_runtime_settings,
            "analysis_mode": analysis_mode,
            "llm_request_options": llm_request_options,
            "bound_documents": bound_documents,
            "knowledge_context": knowledge_context,
            "rule_screening": rule_screening,
            "finding_payloads": finding_payloads,
        }

    def _build_routing_summary(
        self,
        *,
        selected_ids: list[str],
        experts_by_id: dict[str, ExpertProfile],
        skipped_experts: list[dict[str, object]],
        effective_experts: list[dict[str, object]],
        system_added_experts: list[dict[str, object]],
    ) -> dict[str, object]:
        """把专家路由结果整理成 review metadata 和前端可读的结构。"""
        user_selected_experts = [
            {
                "expert_id": expert_id,
                "expert_name": experts_by_id.get(expert_id).name_zh if experts_by_id.get(expert_id) else expert_id,
            }
            for expert_id in selected_ids
        ]
        return {
            "user_selected_experts": user_selected_experts,
            "skipped_experts": skipped_experts,
            "effective_experts": effective_experts,
            "system_added_experts": system_added_experts,
            "fallback_expert_added": bool(system_added_experts),
        }

    def _build_expert_selection_summary(self, selection_plan: dict[str, object]) -> str:
        """生成“本次 MR 由哪些专家参与”的主 Agent 播报文案。"""
        llm_mode = str((selection_plan.get("llm") or {}).get("mode") or "").strip().lower()
        if llm_mode == "user_selected_direct":
            selected_names = [
                str(item.get("expert_name") or item.get("expert_id") or "").strip()
                for item in list(selection_plan.get("selected_experts", []) or [])
                if isinstance(item, dict)
            ]
            selected_names = [item for item in selected_names if item]
            if selected_names:
                return f"用户已手动选择专家，本轮将直接按用户选择执行审核：{'、'.join(selected_names)}。"
            return "用户已手动选择专家，本轮将直接按用户选择执行审核。"
        selected = [
            item
            for item in list(selection_plan.get("selected_experts", []) or [])
            if isinstance(item, dict)
        ]
        skipped = [
            item
            for item in list(selection_plan.get("skipped_experts", []) or [])
            if isinstance(item, dict)
        ]
        if not selected:
            return "大模型未返回有效专家集合，本次审核将使用兜底专家集合继续执行。"
        selected_text = "；".join(
            [
                f"{str(item.get('expert_name') or item.get('expert_id') or '').strip()}：{str(item.get('reason') or '与当前 MR 相关').strip()}"
                for item in selected[:6]
            ]
        )
        skipped_text = "；".join(
            [
                f"{str(item.get('expert_name') or item.get('expert_id') or '').strip()}：{str(item.get('reason') or '本轮无需参与').strip()}"
                for item in skipped[:4]
            ]
        )
        if skipped_text:
            return f"大模型已完成专家参与判定。本次参与审核的专家为：{selected_text}。未纳入本轮的专家包括：{skipped_text}。"
        return f"大模型已完成专家参与判定。本次参与审核的专家为：{selected_text}。"

    def _build_manual_expert_selection_plan(
        self,
        *,
        requested_expert_ids: list[str],
        enabled_experts: list[ExpertProfile],
    ) -> dict[str, object]:
        enabled_by_id = {expert.expert_id: expert for expert in enabled_experts}
        selected_ids = [expert_id for expert_id in requested_expert_ids if expert_id in enabled_by_id]
        selected_experts = [
            {
                "expert_id": expert_id,
                "expert_name": enabled_by_id[expert_id].name_zh,
                "reason": "用户手动选择，直接执行",
                "confidence": 1.0,
            }
            for expert_id in selected_ids
        ]
        skipped_experts = [
            {
                "expert_id": expert_id,
                "reason": "该专家当前不可用或未启用，已跳过。",
            }
            for expert_id in requested_expert_ids
            if expert_id not in enabled_by_id
        ]
        return {
            "requested_expert_ids": list(requested_expert_ids),
            "candidate_expert_ids": [expert.expert_id for expert in enabled_experts],
            "selected_expert_ids": selected_ids,
            "selected_experts": selected_experts,
            "skipped_experts": skipped_experts,
            "llm": {
                "provider": "",
                "model": "",
                "base_url": "",
                "api_key_env": "",
                "mode": "user_selected_direct",
                "error": "",
            },
        }

    def _ensure_required_mr_experts(
        self,
        *,
        subject: ReviewSubject,
        selection_plan: dict[str, object],
        enabled_experts: list[ExpertProfile],
    ) -> dict[str, object]:
        """MR 任务必须保留关联影响专家，避免 LLM 自动选择时把它排除。"""

        if subject.subject_type != "mr":
            return selection_plan
        if str((selection_plan.get("llm") or {}).get("mode") or "").strip().lower() == "user_selected_direct":
            return selection_plan
        enabled_by_id = {expert.expert_id: expert for expert in enabled_experts}
        impact_expert = enabled_by_id.get(CHANGE_IMPACT_EXPERT_ID)
        if impact_expert is None:
            return selection_plan
        selected_ids = [
            str(expert_id).strip()
            for expert_id in list(selection_plan.get("selected_expert_ids", []) or [])
            if str(expert_id).strip()
        ]
        selected_entries = [
            dict(item)
            for item in list(selection_plan.get("selected_experts", []) or [])
            if isinstance(item, dict)
        ]
        if CHANGE_IMPACT_EXPERT_ID not in selected_ids:
            selected_ids.append(CHANGE_IMPACT_EXPERT_ID)
            selected_entries.append(
                {
                    "expert_id": CHANGE_IMPACT_EXPERT_ID,
                    "expert_name": impact_expert.name_zh,
                    "reason": "每个 MR 都需要输出关联影响报告，系统固定保留关联性影响分析专家。",
                    "confidence": 1.0,
                    "source": "system_required",
                }
            )
        skipped_entries = [
            dict(item)
            for item in list(selection_plan.get("skipped_experts", []) or [])
            if isinstance(item, dict) and str(item.get("expert_id") or "") != CHANGE_IMPACT_EXPERT_ID
        ]
        requested_ids = [
            str(expert_id).strip()
            for expert_id in list(selection_plan.get("requested_expert_ids", []) or [])
            if str(expert_id).strip()
        ]
        if CHANGE_IMPACT_EXPERT_ID not in requested_ids:
            requested_ids.append(CHANGE_IMPACT_EXPERT_ID)
        return {
            **selection_plan,
            "requested_expert_ids": requested_ids,
            "selected_expert_ids": selected_ids,
            "selected_experts": selected_entries,
            "skipped_experts": skipped_entries,
        }

    def _ensure_thorough_review_core_experts(
        self,
        *,
        subject: ReviewSubject,
        selection_plan: dict[str, object],
        enabled_experts: list[ExpertProfile],
        runtime_settings,
    ) -> dict[str, object]:
        """In thorough review mode, add core quality experts instead of trusting sparse routing."""

        mode = str(getattr(runtime_settings, "review_quality_mode", "") or "").strip().lower()
        if mode != "thorough_review":
            return selection_plan
        if subject.subject_type != "mr":
            return selection_plan
        if str((selection_plan.get("llm") or {}).get("mode") or "").strip().lower() == "user_selected_direct":
            return selection_plan

        enabled_by_id = {expert.expert_id: expert for expert in enabled_experts if expert.enabled}
        selected_ids = [
            str(expert_id).strip()
            for expert_id in list(selection_plan.get("selected_expert_ids", []) or [])
            if str(expert_id).strip()
        ]
        selected_entries = [
            dict(item)
            for item in list(selection_plan.get("selected_experts", []) or [])
            if isinstance(item, dict)
        ]
        selected_entry_ids = {
            str(item.get("expert_id") or "").strip()
            for item in selected_entries
            if str(item.get("expert_id") or "").strip()
        }
        requested_ids = [
            str(expert_id).strip()
            for expert_id in list(selection_plan.get("requested_expert_ids", []) or [])
            if str(expert_id).strip()
        ]
        added_ids: list[str] = []
        for expert_id in THOROUGH_REVIEW_CORE_EXPERT_IDS:
            expert = enabled_by_id.get(expert_id)
            if expert is None:
                continue
            if expert_id not in selected_ids:
                selected_ids.append(expert_id)
                added_ids.append(expert_id)
            if expert_id not in selected_entry_ids:
                selected_entries.append(
                    {
                        "expert_id": expert_id,
                        "expert_name": expert.name_zh,
                        "reason": "深度检视模式固定补入核心质量专家，避免预筛或弱模型路由漏检。",
                        "confidence": 1.0,
                        "source": "thorough_review_required",
                    }
                )
                selected_entry_ids.add(expert_id)
            if expert_id not in requested_ids:
                requested_ids.append(expert_id)

        if not added_ids:
            return selection_plan

        skipped_entries = [
            dict(item)
            for item in list(selection_plan.get("skipped_experts", []) or [])
            if isinstance(item, dict)
            and str(item.get("expert_id") or "").strip() not in set(added_ids)
        ]
        return {
            **selection_plan,
            "requested_expert_ids": requested_ids,
            "selected_expert_ids": selected_ids,
            "selected_experts": selected_entries,
            "skipped_experts": skipped_entries,
            "thorough_review_added_expert_ids": added_ids,
        }

    def _should_skip_expert_route(
        self,
        *,
        subject: ReviewSubject,
        primary_route: dict[str, object],
        runtime_settings,
    ) -> bool:
        """Decide whether routing may suppress a selected expert."""

        if bool(primary_route.get("routeable", True)):
            return False
        if subject.subject_type == "mr" and str(
            getattr(runtime_settings, "review_quality_mode", "") or ""
        ).strip().lower() == "thorough_review":
            return False
        return True

    def _build_manual_routing_plan(
        self,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
    ) -> dict[str, dict[str, object]]:
        plan: dict[str, dict[str, object]] = {}
        for expert in experts:
            plan[expert.expert_id] = {
                "expert_id": expert.expert_id,
                "routeable": True,
                "reason": "用户已手动选择专家，系统直接进入全量 hunk 审查。",
                "file_path": self._pick_file_path(subject, expert.expert_id),
                "line_start": 1,
                "routing_llm": {
                    "mode": "user_selected_direct",
                    "provider": "",
                    "model": "",
                },
            }
        return plan

    def _build_routing_summary_message(self, routing_summary: dict[str, object]) -> str:
        """生成前端提示条和事件时间线都会复用的路由摘要文案。"""
        skipped = routing_summary.get("skipped_experts", [])
        added = routing_summary.get("system_added_experts", [])
        skipped_names = "、".join(
            [
                str(item.get("expert_name") or item.get("expert_id") or "")
                for item in skipped
                if isinstance(item, dict)
            ]
        )
        added_names = "、".join(
            [
                str(item.get("expert_name") or item.get("expert_id") or "")
                for item in added
                if isinstance(item, dict)
            ]
        )
        if skipped_names and added_names:
            return f"{skipped_names} 与当前变更相关性较低，系统已自动补入 {added_names} 继续审查。"
        if skipped_names:
            return f"{skipped_names} 与当前变更相关性较低，已跳过本轮审查。"
        return "本轮专家路由已完成。"

    def _execute_expert_jobs(
        self,
        expert_jobs: list[dict[str, object]],
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
    ) -> list[dict[str, object]]:
        """按分析模式执行专家任务。

        标准模式允许更高并发；轻量模式会压低并发，减少内网/Windows 下的大模型并发压力。
        """
        failures: list[dict[str, object]] = []
        if not expert_jobs:
            return failures
        if os.getenv("PYTEST_CURRENT_TEST") or len(expert_jobs) <= 1:
            for job in expert_jobs:
                self._update_expert_review_progress(job, state="started", total_jobs=len(expert_jobs))
                try:
                    self._run_expert_from_command(**job)
                    self._update_expert_review_progress(job, state="completed", total_jobs=len(expert_jobs))
                except ReviewClosedError:
                    raise
                except Exception as exc:
                    failures.append(self._record_expert_job_failure(job, exc))
                    self._update_expert_review_progress(job, state="failed", total_jobs=len(expert_jobs))
                finally:
                    self._release_expert_job_payload(job)
            return failures
        max_workers = min(self._max_parallel_experts(runtime_settings, analysis_mode), len(expert_jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for job in expert_jobs:
                self._update_expert_review_progress(job, state="started", total_jobs=len(expert_jobs))
                futures.append((job, executor.submit(self._run_expert_from_command, **job)))
            for job, future in futures:
                try:
                    future.result()
                    self._update_expert_review_progress(job, state="completed", total_jobs=len(expert_jobs))
                except ReviewClosedError:
                    raise
                except Exception as exc:
                    failures.append(self._record_expert_job_failure(job, exc))
                    self._update_expert_review_progress(job, state="failed", total_jobs=len(expert_jobs))
                finally:
                    self._release_expert_job_payload(job)
        return failures

    def _build_expert_route_hints(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        candidate_hunks: list[dict[str, object]],
        *,
        primary_route: dict[str, object],
    ) -> list[dict[str, object]]:
        """让单个专家覆盖当前审核任务中的全部候选 hunk，并按文件聚合为少量批次任务。"""

        if not candidate_hunks:
            return [dict(primary_route or {})]

        grouped_hunks: dict[str, list[dict[str, object]]] = {}
        for item in candidate_hunks:
            file_path = str(item.get("file_path") or "").strip()
            if not file_path:
                continue
            grouped_hunks.setdefault(file_path, []).append(item)
        base_confidence = float(primary_route.get("confidence") or 0.31)
        base_reason = str(primary_route.get("routing_reason") or "").strip()
        route_hints: list[dict[str, object]] = []
        for file_path, grouped_items in sorted(grouped_hunks.items(), key=lambda pair: pair[0]):
            primary_item = sorted(grouped_items, key=lambda item: int(item.get("line_start") or 1))[0]
            line_start = int(primary_item.get("line_start") or 1)
            target_hunks = [
                {
                    "file_path": file_path,
                    "hunk_header": str(item.get("hunk_header") or ""),
                    "start_line": int(item.get("start_line") or item.get("line_start") or 1),
                    "end_line": int(item.get("end_line") or item.get("line_start") or 1),
                    "changed_lines": [
                        int(value)
                        for value in list(item.get("changed_lines") or [])
                        if isinstance(value, int)
                    ]
                    or [int(item.get("line_start") or 1)],
                    "excerpt": str(item.get("excerpt") or ""),
                    "risk_signals": [
                        str(value).strip()
                        for value in list(item.get("risk_signals") or [])
                        if str(value).strip()
                    ],
                }
                for item in sorted(grouped_items, key=lambda item: int(item.get("line_start") or 1))
            ]
            risk_signals = list(
                dict.fromkeys(
                    str(value).strip()
                    for item in grouped_items
                    for value in list(item.get("risk_signals") or [])
                    if str(value).strip()
                )
            )
            merged_repo_hits: dict[str, object] = {}
            for item in grouped_items:
                for key, value in dict(item.get("repo_hits") or {}).items():
                    if key not in merged_repo_hits and value not in (None, "", [], {}):
                        merged_repo_hits[key] = value
            route_hints.append(
                {
                    "expert_id": expert.expert_id,
                    "file_path": file_path,
                    "line_start": line_start,
                    "target_hunk": dict(target_hunks[0]),
                    "target_hunks": target_hunks,
                    "risk_signals": risk_signals,
                    "repo_hits": merged_repo_hits,
                    "routeable": True,
                    "skip_reason": "",
                    "confidence": base_confidence,
                    "routing_reason": base_reason
                    or f"{expert.name_zh} 需要覆盖文件 {file_path} 内的 {len(target_hunks)} 个变更 hunk，并从其专业视角统一审查。",
                    "routing_source": "all_hunks",
                }
            )
        return route_hints

    def _release_expert_job_payload(self, job: dict[str, object]) -> None:
        """专家任务完成后尽快丢弃大对象，降低批次执行期间的峰值内存。"""

        for key in (
            "bound_documents",
            "knowledge_context",
            "rule_screening",
            "repository_context",
            "target_hunk",
            "target_hunks",
            "related_files",
            "business_changed_files",
            "expected_checks",
            "disallowed_inference",
            "batch_items",
        ):
            job.pop(key, None)
        self._maybe_collect_garbage()

    def _maybe_collect_garbage(self) -> None:
        """避免每个任务都触发全量 GC，减少 Windows 场景的长时间停顿。"""
        if sys.platform != "win32":
            return
        if os.getenv("REVIEW_FORCE_GC_WINDOWS", "0").strip() not in {"1", "true", "TRUE", "True"}:
            return
        now = time.monotonic()
        if now - float(self._last_gc_at or 0.0) < self._gc_interval_seconds:
            return
        self._last_gc_at = now
        gc.collect()

    def _max_files_per_expert_call(
        self,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
    ) -> int:
        """单次专家 LLM 调用允许覆盖的最大文件数。"""
        env_key = (
            "REVIEW_EXPERT_MAX_FILES_PER_CALL_LIGHT"
            if analysis_mode == "light"
            else "REVIEW_EXPERT_MAX_FILES_PER_CALL_STANDARD"
        )
        default_value = 3 if analysis_mode == "light" else 2
        raw = os.getenv(env_key, "").strip()
        if raw:
            try:
                return max(1, min(8, int(raw)))
            except ValueError:
                return default_value
        return default_value

    def _batch_expert_jobs(
        self,
        jobs: list[dict[str, object]],
        *,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
    ) -> list[dict[str, object]]:
        """把同一专家的多文件任务压缩成更少的 LLM 调用批次。"""
        if len(jobs) <= 1:
            return jobs
        max_files = self._max_files_per_expert_call(runtime_settings, analysis_mode)
        if max_files <= 1:
            return jobs

        token_budget = self._resolve_expert_call_token_budget(runtime_settings, analysis_mode)
        batched_jobs: list[dict[str, object]] = []
        current_chunk: list[dict[str, object]] = []
        current_chunk_tokens = 0
        for item in jobs:
            estimate = self._estimate_expert_job_tokens(item)
            if estimate > token_budget:
                if current_chunk:
                    batched_jobs.append(
                        self._merge_expert_job_chunk(
                            current_chunk,
                            estimated_tokens_sum=current_chunk_tokens,
                        )
                    )
                    current_chunk = []
                    current_chunk_tokens = 0
                oversized_splits = self._split_oversized_expert_job(item, token_budget=token_budget)
                for split in oversized_splits:
                    batched_jobs.append(
                        self._merge_expert_job_chunk(
                            [split],
                            estimated_tokens_sum=self._estimate_expert_job_tokens(split),
                        )
                    )
                continue
            should_flush = bool(
                current_chunk
                and (
                    len(current_chunk) >= max_files
                    or current_chunk_tokens + estimate > token_budget
                )
            )
            if should_flush:
                batched_jobs.append(
                    self._merge_expert_job_chunk(
                        current_chunk,
                        estimated_tokens_sum=current_chunk_tokens,
                    )
                )
                current_chunk = []
                current_chunk_tokens = 0

            current_chunk.append(item)
            current_chunk_tokens += estimate

        if current_chunk:
            batched_jobs.append(
                self._merge_expert_job_chunk(
                    current_chunk,
                    estimated_tokens_sum=current_chunk_tokens,
                )
            )

        return batched_jobs

    def _merge_expert_job_chunk(
        self,
        chunk: list[dict[str, object]],
        *,
        estimated_tokens_sum: int,
    ) -> dict[str, object]:
        if len(chunk) == 1:
            single = dict(chunk[0])
            single["batch_file_count"] = 1
            single["batch_hunk_count"] = len([item for item in list(single.get("target_hunks") or []) if isinstance(item, dict)])
            single["batch_token_estimate"] = int(estimated_tokens_sum or self._estimate_expert_job_tokens(single))
            single["code_graph_priority_score"] = float(single.get("code_graph_priority_score") or 0.0)
            return single

        primary = dict(chunk[0])
        batch_items: list[dict[str, object]] = []
        merged_related_files: list[str] = []
        merged_target_hunks: list[dict[str, object]] = []
        merged_repository_context: dict[str, object] = {}
        merged_bound_documents: list[object] = []
        merged_knowledge_context: dict[str, object] = {}
        merged_rule_screening: dict[str, object] = {}

        for item in chunk:
            file_path = str(item.get("file_path") or "").strip()
            line_start = int(item.get("line_start") or 1)
            repo_context = dict(item.get("repository_context") or {})
            target_hunk = dict(item.get("target_hunk") or {})
            target_hunks = [
                dict(hunk)
                for hunk in list(item.get("target_hunks") or [])
                if isinstance(hunk, dict)
            ] or ([dict(target_hunk)] if target_hunk else [])
            related_files = [str(value).strip() for value in list(item.get("related_files") or []) if str(value).strip()]
            for path in related_files + ([file_path] if file_path else []):
                if path and path not in merged_related_files:
                    merged_related_files.append(path)
            merged_target_hunks.extend(target_hunks)
            merged_repository_context = self._merge_repository_context_for_batch(merged_repository_context, repo_context)
            merged_bound_documents = self._merge_bound_documents_for_batch(
                merged_bound_documents,
                list(item.get("bound_documents") or []),
            )
            merged_knowledge_context = self._merge_knowledge_context_for_batch(
                merged_knowledge_context,
                dict(item.get("knowledge_context") or {}),
            )
            merged_rule_screening = self._merge_rule_screening_for_batch(
                merged_rule_screening,
                dict(item.get("rule_screening") or {}),
            )
            batch_items.append(
                {
                    "file_path": file_path,
                    "line_start": line_start,
                    "repository_context": repo_context,
                    "target_hunk": target_hunk,
                    "target_hunks": target_hunks,
                    "related_files": related_files,
                    "code_graph_priority_score": float(item.get("code_graph_priority_score") or 0.0),
                }
            )

        primary["batch_items"] = batch_items
        primary["file_path"] = str(batch_items[0].get("file_path") or primary.get("file_path") or "")
        primary["line_start"] = int(batch_items[0].get("line_start") or primary.get("line_start") or 1)
        primary["target_hunk"] = dict(batch_items[0].get("target_hunk") or primary.get("target_hunk") or {})
        primary["target_hunks"] = merged_target_hunks
        primary["related_files"] = merged_related_files
        primary["repository_context"] = merged_repository_context
        primary["bound_documents"] = merged_bound_documents
        primary["knowledge_context"] = merged_knowledge_context
        primary["rule_screening"] = merged_rule_screening
        primary["batch_file_count"] = len(batch_items)
        primary["batch_hunk_count"] = len(merged_target_hunks)
        primary["batch_token_estimate"] = int(max(0, estimated_tokens_sum))
        primary["code_graph_priority_score"] = max(
            [float(item.get("code_graph_priority_score") or 0.0) for item in batch_items] or [0.0]
        )
        routing_reason = str(primary.get("routing_reason") or "").strip()
        if routing_reason:
            primary["routing_reason"] = f"{routing_reason}；本次批量覆盖 {len(batch_items)} 个文件。"
        return primary

    def _sort_expert_jobs_by_code_graph_priority(self, jobs: list[dict[str, object]]) -> list[dict[str, object]]:
        if not jobs:
            return []
        return sorted(
            jobs,
            key=lambda item: (
                float(item.get("code_graph_priority_score") or 0.0),
                float(item.get("routing_confidence") or 0.0),
            ),
            reverse=True,
        )

    def _code_graph_priority_score(
        self,
        repository_context: dict[str, object],
        file_path: str,
        line_start: int,
    ) -> float:
        impact = dict(repository_context.get("code_graph_impact_analysis") or {})
        priorities = [
            dict(item)
            for item in list(impact.get("review_priorities") or [])
            if isinstance(item, dict)
        ]
        normalized_file = str(file_path or "").strip().replace("\\", "/")
        best = 0.0
        for item in priorities:
            priority_file = str(item.get("file_path") or "").strip().replace("\\", "/")
            if priority_file and normalized_file and priority_file != normalized_file:
                continue
            try:
                priority_line = int(item.get("line_start") or 0)
            except (TypeError, ValueError):
                priority_line = 0
            if priority_line and abs(priority_line - int(line_start or 1)) > 80:
                continue
            best = max(best, float(item.get("risk_score") or 0.0))
        if best <= 0.0:
            best = float(impact.get("risk_score") or 0.0)
        return round(max(0.0, min(best, 1.0)), 4)

    def _resolve_expert_call_token_budget(
        self,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
    ) -> int:
        reserve_raw = str(os.getenv("REVIEW_EXPERT_PROMPT_TOKEN_RESERVE", "")).strip()
        if reserve_raw:
            try:
                reserve_tokens = max(2_000, min(40_000, int(reserve_raw)))
            except ValueError:
                reserve_tokens = 18_000
        else:
            reserve_tokens = 18_000
        if analysis_mode == "light":
            configured = int(getattr(runtime_settings, "light_llm_max_input_tokens", 0) or 0)
            base_limit = configured if configured > 0 else 110_000
        else:
            raw = str(os.getenv("REVIEW_STANDARD_MAX_INPUT_TOKENS", "")).strip()
            if raw:
                try:
                    base_limit = max(32_000, min(200_000, int(raw)))
                except ValueError:
                    base_limit = 131_072
            else:
                base_limit = 131_072
        return max(12_000, base_limit - reserve_tokens)

    def _estimate_expert_job_tokens(self, job: dict[str, object]) -> int:
        parts = [
            str(job.get("file_path") or ""),
            str(job.get("line_start") or ""),
            self._serialize_for_token_estimate(job.get("repository_context"), max_chars=24_000),
            self._serialize_for_token_estimate(job.get("target_hunks"), max_chars=28_000),
            self._serialize_for_token_estimate(job.get("rule_screening"), max_chars=20_000),
            self._serialize_for_token_estimate(job.get("knowledge_context"), max_chars=8_000),
            self._serialize_for_token_estimate(job.get("bound_documents"), max_chars=18_000),
            self._serialize_for_token_estimate(job.get("expected_checks"), max_chars=4_000),
            self._serialize_for_token_estimate(job.get("disallowed_inference"), max_chars=4_000),
        ]
        total_chars = sum(len(part) for part in parts if part)
        return max(600, int(total_chars / 3.8) + 600)

    def _serialize_for_token_estimate(self, value: object, *, max_chars: int) -> str:
        if value in (None, "", [], {}):
            return ""
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    def _split_oversized_expert_job(
        self,
        job: dict[str, object],
        *,
        token_budget: int,
    ) -> list[dict[str, object]]:
        base_job = dict(job or {})
        file_path = str(base_job.get("file_path") or "").strip()
        target_hunks = [
            dict(item)
            for item in list(base_job.get("target_hunks") or [])
            if isinstance(item, dict)
        ]
        if len(target_hunks) <= 1:
            return [base_job]

        max_hunks_raw = str(os.getenv("REVIEW_EXPERT_MAX_HUNKS_PER_CALL", "")).strip()
        if max_hunks_raw:
            try:
                max_hunks_per_call = max(1, min(80, int(max_hunks_raw)))
            except ValueError:
                max_hunks_per_call = 12
        else:
            max_hunks_per_call = 12

        chunks: list[list[dict[str, object]]] = []
        current: list[dict[str, object]] = []
        for hunk in target_hunks:
            probe = current + [hunk]
            probe_job = self._build_split_job(base_job, file_path=file_path, split_hunks=probe)
            probe_tokens = self._estimate_expert_job_tokens(probe_job)
            if current and (probe_tokens > token_budget or len(probe) > max_hunks_per_call):
                chunks.append(list(current))
                current = [hunk]
            else:
                current = probe
        if current:
            chunks.append(list(current))

        split_jobs = [
            self._build_split_job(base_job, file_path=file_path, split_hunks=chunk_hunks)
            for chunk_hunks in chunks
            if chunk_hunks
        ]
        return split_jobs or [base_job]

    def _build_split_job(
        self,
        base_job: dict[str, object],
        *,
        file_path: str,
        split_hunks: list[dict[str, object]],
    ) -> dict[str, object]:
        split_job = dict(base_job)
        first_hunk = dict(split_hunks[0] or {})
        split_line_start = int(first_hunk.get("start_line") or first_hunk.get("line_start") or base_job.get("line_start") or 1)
        compact_repo_context = self._slice_repository_context_for_hunks(
            dict(base_job.get("repository_context") or {}),
            split_hunks=split_hunks,
            file_path=file_path,
        )
        split_job["line_start"] = split_line_start
        split_job["target_hunk"] = first_hunk
        split_job["target_hunks"] = [dict(item) for item in split_hunks]
        split_job["repository_context"] = compact_repo_context
        split_job["batch_items"] = [
            {
                "file_path": file_path,
                "line_start": split_line_start,
                "repository_context": compact_repo_context,
                "target_hunk": first_hunk,
                "target_hunks": [dict(item) for item in split_hunks],
                "related_files": [str(value).strip() for value in list(base_job.get("related_files") or []) if str(value).strip()],
            }
        ]
        split_job["batch_file_count"] = 1
        split_job["batch_hunk_count"] = len(split_hunks)
        split_job["routing_reason"] = (
            f"{str(base_job.get('routing_reason') or '').strip()}；"
            f"因上下文预算限制，本文件已拆分为分批审查（当前批 {len(split_hunks)} 个 hunk）。"
        ).strip("；")
        return split_job

    def _slice_repository_context_for_hunks(
        self,
        repository_context: dict[str, object],
        *,
        split_hunks: list[dict[str, object]],
        file_path: str,
    ) -> dict[str, object]:
        if not repository_context:
            return {}
        anchor_lines = []
        for hunk in split_hunks:
            value = int(hunk.get("start_line") or hunk.get("line_start") or 0)
            if value > 0:
                anchor_lines.append(value)
        line_anchor = min(anchor_lines) if anchor_lines else 0

        compact = dict(repository_context)
        compact["context_files"] = self._compact_context_files(
            list(repository_context.get("context_files") or []),
            file_path=file_path,
        )
        for key in (
            "related_contexts",
            "related_source_snippets",
            "caller_contexts",
            "callee_contexts",
            "domain_model_contexts",
            "persistence_contexts",
            "symbol_contexts",
            "related_code_snippets",
        ):
            compact[key] = self._compact_context_entries(
                list(repository_context.get(key) or []),
                file_path=file_path,
                line_anchor=line_anchor,
            )
        return compact

    def _compact_context_files(self, values: list[object], *, file_path: str) -> list[str]:
        normalized: list[str] = []
        for item in values:
            value = str(item or "").strip()
            if value and value not in normalized:
                normalized.append(value)
        if file_path:
            normalized = [file_path] + [item for item in normalized if item != file_path]
        return normalized[:10]

    def _compact_context_entries(
        self,
        values: list[object],
        *,
        file_path: str,
        line_anchor: int,
    ) -> list[dict[str, object]]:
        entries = [dict(item) for item in values if isinstance(item, dict)]
        if not entries:
            return []
        same_file: list[dict[str, object]] = []
        others: list[dict[str, object]] = []
        for entry in entries:
            entry_path = str(
                entry.get("path")
                or entry.get("file_path")
                or entry.get("relative_path")
                or ""
            ).strip()
            if file_path and entry_path and entry_path == file_path:
                same_file.append(entry)
            else:
                others.append(entry)

        def _distance(entry: dict[str, object]) -> int:
            line_value = int(entry.get("line_start") or entry.get("line") or 0)
            if line_anchor <= 0 or line_value <= 0:
                return 10**9
            return abs(line_value - line_anchor)

        same_file_sorted = sorted(same_file, key=_distance)
        merged = same_file_sorted[:5] + others[:3]
        return merged[:8]

    def _merge_repository_context_for_batch(
        self,
        current: dict[str, object],
        incoming: dict[str, object],
    ) -> dict[str, object]:
        merged = dict(current or {})
        candidate = dict(incoming or {})
        for key, value in candidate.items():
            if value in (None, "", [], {}):
                continue
            if key == "review_observations":
                existing = self._normalize_review_observations(merged.get(key))
                seen = {
                    (
                        str(item.get("observation_id") or "").strip(),
                        str(item.get("file_path") or "").strip(),
                        int(self._normalize_optional_line_value(item.get("line_start")) or 1),
                        str(item.get("kind") or "").strip(),
                    )
                    for item in existing
                }
                for item in self._normalize_review_observations(value):
                    marker = (
                        str(item.get("observation_id") or "").strip(),
                        str(item.get("file_path") or "").strip(),
                        int(self._normalize_optional_line_value(item.get("line_start")) or 1),
                        str(item.get("kind") or "").strip(),
                    )
                    if marker in seen:
                        continue
                    existing.append(dict(item))
                    seen.add(marker)
                merged[key] = existing[:20]
                continue
            if key == "java_quality_signals":
                existing = [
                    str(item).strip()
                    for item in list(merged.get(key) or [])
                    if str(item).strip()
                ]
                for item in list(value or []):
                    signal = str(item).strip()
                    if signal and signal not in existing:
                        existing.append(signal)
                merged[key] = existing[:16]
                continue
            if key == "related_code_snippets":
                existing = [dict(item) for item in list(merged.get(key) or []) if isinstance(item, dict)]
                seen = {
                    (
                        str(item.get("path") or "").strip(),
                        int(item.get("line_start") or 0),
                        str(item.get("kind") or "").strip(),
                    )
                    for item in existing
                }
                for item in list(value or []):
                    if not isinstance(item, dict):
                        continue
                    marker = (
                        str(item.get("path") or "").strip(),
                        int(item.get("line_start") or 0),
                        str(item.get("kind") or "").strip(),
                    )
                    if marker in seen:
                        continue
                    existing.append(dict(item))
                    seen.add(marker)
                merged[key] = existing[:20]
                continue
            if key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value
        return merged

    def _merge_bound_documents_for_batch(self, current: list[object], incoming: list[object]) -> list[object]:
        merged = list(current or [])
        seen = {
            str(getattr(item, "doc_id", "") or getattr(item, "title", "") or "").strip()
            for item in merged
        }
        for item in list(incoming or []):
            marker = str(getattr(item, "doc_id", "") or getattr(item, "title", "") or "").strip()
            if marker and marker in seen:
                continue
            merged.append(item)
            if marker:
                seen.add(marker)
        return merged[:16]

    def _merge_knowledge_context_for_batch(
        self,
        current: dict[str, object],
        incoming: dict[str, object],
    ) -> dict[str, object]:
        merged = dict(current or {})
        candidate = dict(incoming or {})
        changed_files = [str(item).strip() for item in list(merged.get("changed_files") or []) if str(item).strip()]
        for item in list(candidate.get("changed_files") or []):
            path = str(item).strip()
            if path and path not in changed_files:
                changed_files.append(path)
        if changed_files:
            merged["changed_files"] = changed_files[:20]
        query_terms = [str(item).strip() for item in list(merged.get("query_terms") or []) if str(item).strip()]
        for item in list(candidate.get("query_terms") or []):
            term = str(item).strip()
            if term and term not in query_terms:
                query_terms.append(term)
        if query_terms:
            merged["query_terms"] = query_terms[:64]
        knowledge_sources = [
            str(item).strip() for item in list(merged.get("knowledge_sources") or []) if str(item).strip()
        ]
        for item in list(candidate.get("knowledge_sources") or []):
            source = str(item).strip()
            if source and source not in knowledge_sources:
                knowledge_sources.append(source)
        if knowledge_sources:
            merged["knowledge_sources"] = knowledge_sources[:16]
        for key in ("subject_title", "subject_type", "focus_file", "focus_line"):
            if key not in merged and key in candidate:
                merged[key] = candidate.get(key)
        return merged

    def _merge_rule_screening_for_batch(
        self,
        current: dict[str, object],
        incoming: dict[str, object],
    ) -> dict[str, object]:
        merged = dict(current or {})
        candidate = dict(incoming or {})
        merged_rules = [dict(item) for item in list(merged.get("matched_rules_for_llm") or []) if isinstance(item, dict)]
        seen_rule_ids = {str(item.get("rule_id") or "").strip() for item in merged_rules}
        for item in list(candidate.get("matched_rules_for_llm") or []):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or "").strip()
            if rule_id and rule_id in seen_rule_ids:
                continue
            merged_rules.append(dict(item))
            if rule_id:
                seen_rule_ids.add(rule_id)
        if merged_rules:
            merged["matched_rules_for_llm"] = merged_rules[:40]
            merged["matched_rule_count"] = len(merged["matched_rules_for_llm"])
        merged["total_rules"] = max(int(merged.get("total_rules") or 0), int(candidate.get("total_rules") or 0))
        merged["enabled_rules"] = max(int(merged.get("enabled_rules") or 0), int(candidate.get("enabled_rules") or 0))
        merged["must_review_count"] = max(
            int(merged.get("must_review_count") or 0),
            int(candidate.get("must_review_count") or 0),
        )
        merged["possible_hit_count"] = max(
            int(merged.get("possible_hit_count") or 0),
            int(candidate.get("possible_hit_count") or 0),
        )
        return merged

    def _get_cached_knowledge_payload(
        self,
        *,
        review_id: str,
        expert_id: str,
        file_path: str,
        analysis_mode: str,
    ) -> dict[str, object] | None:
        key = (str(review_id).strip(), str(expert_id).strip(), str(file_path).strip(), str(analysis_mode).strip())
        payload = self._knowledge_runtime_cache.get(key)
        return dict(payload) if isinstance(payload, dict) else None

    def _set_cached_knowledge_payload(
        self,
        *,
        review_id: str,
        expert_id: str,
        file_path: str,
        analysis_mode: str,
        knowledge_context: dict[str, object],
        bound_documents: list[object],
        rule_screening: dict[str, object],
    ) -> None:
        key = (str(review_id).strip(), str(expert_id).strip(), str(file_path).strip(), str(analysis_mode).strip())
        self._knowledge_runtime_cache[key] = {
            "knowledge_context": dict(knowledge_context or {}),
            "bound_documents": list(bound_documents or []),
            "rule_screening": dict(rule_screening or {}),
        }

    def _prepare_knowledge_runtime_inputs(
        self,
        *,
        review_id: str,
        expert_id: str,
        file_path: str,
        analysis_mode: str,
        knowledge_context: dict[str, object],
        runtime_settings,
    ) -> tuple[list[object], dict[str, object]]:
        cached = self._get_cached_knowledge_payload(
            review_id=review_id,
            expert_id=expert_id,
            file_path=file_path,
            analysis_mode=analysis_mode,
        )
        if cached is not None:
            return (
                list(cached.get("bound_documents") or []),
                dict(cached.get("rule_screening") or {}),
            )

        bound_documents = self.knowledge_service.retrieve_for_expert(expert_id, knowledge_context)
        rule_screening = self.knowledge_service.screen_rules_for_expert(
            expert_id,
            knowledge_context,
            runtime_settings=runtime_settings,
            analysis_mode=analysis_mode,
            review_id=review_id,
        )
        self._set_cached_knowledge_payload(
            review_id=review_id,
            expert_id=expert_id,
            file_path=file_path,
            analysis_mode=analysis_mode,
            knowledge_context=knowledge_context,
            bound_documents=bound_documents,
            rule_screening=rule_screening,
        )
        return list(bound_documents), dict(rule_screening or {})

    def _prepare_expert_batch_knowledge_inputs(
        self,
        *,
        review_id: str,
        expert_id: str,
        analysis_mode: str,
        route_jobs: list[dict[str, object]],
        runtime_settings,
    ) -> tuple[list[object], dict[str, object]]:
        merged_knowledge_context: dict[str, object] = {}
        file_paths: list[str] = []
        for job in route_jobs:
            merged_knowledge_context = self._merge_knowledge_context_for_batch(
                merged_knowledge_context,
                dict(job.get("knowledge_context") or {}),
            )
            file_path = str(job.get("file_path") or "").strip()
            if file_path and file_path not in file_paths:
                file_paths.append(file_path)

        bound_documents, rule_screening = self._prepare_knowledge_runtime_inputs(
            review_id=review_id,
            expert_id=expert_id,
            file_path="__expert_batch__",
            analysis_mode=analysis_mode,
            knowledge_context=merged_knowledge_context,
            runtime_settings=runtime_settings,
        )
        for file_path in file_paths:
            self._set_cached_knowledge_payload(
                review_id=review_id,
                expert_id=expert_id,
                file_path=file_path,
                analysis_mode=analysis_mode,
                knowledge_context=merged_knowledge_context,
                bound_documents=bound_documents,
                rule_screening=rule_screening,
            )
        return list(bound_documents), dict(rule_screening or {})

    def _update_expert_review_progress(
        self,
        job: dict[str, object],
        *,
        state: Literal["started", "completed", "failed"],
        total_jobs: int,
    ) -> None:
        review = job.get("review")
        expert = job.get("expert")
        if not isinstance(review, ReviewTask) or not isinstance(expert, ExpertProfile):
            return
        latest = self.review_repo.get(review.review_id)
        if latest is None:
            return
        metadata = dict(latest.subject.metadata or {})
        progress = dict(metadata.get("expert_review_progress") or {})
        started_ids = [str(item) for item in list(progress.get("started_expert_ids") or []) if str(item).strip()]
        completed_ids = [str(item) for item in list(progress.get("completed_expert_ids") or []) if str(item).strip()]
        failed_ids = [str(item) for item in list(progress.get("failed_expert_ids") or []) if str(item).strip()]

        expert_id = expert.expert_id
        if state == "started" and expert_id not in started_ids:
            started_ids.append(expert_id)
        if state == "completed" and expert_id not in completed_ids:
            completed_ids.append(expert_id)
        if state == "failed" and expert_id not in failed_ids:
            failed_ids.append(expert_id)

        now = datetime.now(UTC)
        file_path = str(job.get("file_path") or "")
        line_start = int(job.get("line_start") or 1)
        active_expert_id = expert_id if state == "started" else ""
        active_expert_name = expert.name_zh if state == "started" else ""
        progress.update(
            {
                "total_expert_jobs": max(int(progress.get("total_expert_jobs") or 0), int(total_jobs or 0)),
                "started_expert_ids": started_ids,
                "completed_expert_ids": completed_ids,
                "failed_expert_ids": failed_ids,
                "started_count": len(started_ids),
                "completed_count": len(completed_ids),
                "failed_count": len(failed_ids),
                "active_expert_id": active_expert_id,
                "active_expert_name": active_expert_name,
                "last_event": state,
                "last_event_at": now.isoformat(),
                "last_expert_id": expert_id,
                "last_expert_name": expert.name_zh,
                "last_file_path": file_path,
                "last_line_start": line_start,
            }
        )
        latest.subject.metadata = {
            **metadata,
            "expert_review_progress": progress,
        }
        latest.updated_at = now
        self.review_repo.save(latest)

    def _merge_review_metadata(self, review: ReviewTask, metadata_patch: dict[str, object]) -> ReviewTask:
        latest = self.review_repo.get(review.review_id) or review
        latest.subject.metadata = {
            **dict(latest.subject.metadata or {}),
            **metadata_patch,
        }
        latest.updated_at = datetime.now(UTC)
        self.review_repo.save(latest)
        return latest

    def _get_cached_knowledge_preparation(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        file_path: str,
        analysis_mode: str,
        knowledge_context: dict[str, object],
        runtime_settings,
    ) -> tuple[list[object], dict[str, object]]:
        cache_key = (review.review_id, expert.expert_id, str(file_path).strip(), str(analysis_mode).strip())
        cached = self._knowledge_runtime_cache.get(cache_key)
        if cached is not None:
            return (
                list(cached.get("bound_documents") or []),
                dict(cached.get("rule_screening") or {}),
            )

        bound_documents = self.knowledge_service.retrieve_for_expert(expert.expert_id, knowledge_context)
        rule_screening = self.knowledge_service.screen_rules_for_expert(
            expert.expert_id,
            knowledge_context,
            runtime_settings=runtime_settings,
            analysis_mode=analysis_mode,
            review_id=review.review_id,
        )
        self._knowledge_runtime_cache[cache_key] = {
            "bound_documents": list(bound_documents),
            "rule_screening": dict(rule_screening or {}),
        }
        return list(bound_documents), dict(rule_screening or {})

    def _run_expert_from_command(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        command_message: ConversationMessage,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object] | None = None,
        target_hunk: dict[str, object] | None = None,
        target_hunks: list[dict[str, object]] | None = None,
        related_files: list[str] | None = None,
        business_changed_files: list[str] | None = None,
        expected_checks: list[str] | None = None,
        disallowed_inference: list[str] | None = None,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
        llm_request_options: dict[str, int | float],
        bound_documents: list[object],
        knowledge_context: dict[str, object],
        rule_screening: dict[str, object],
        finding_payloads: list[dict[str, object]],
        batch_items: list[dict[str, object]] | None = None,
        **_: object,
    ) -> None:
        """执行单个专家任务。

        关键顺序：
        1. 收集 verifier/tool 证据
        2. 调用运行时工具
        3. 发送 expert_ack / tool 消息
        4. 拼接 prompt 调用 LLM
        5. 解析并稳定化 finding
        6. 落库 finding、analysis message 和 event
        """
        self._abort_if_closed(review.review_id)
        MemoryProbe.log(
            "expert.start",
            review_id=review.review_id,
            expert_id=expert.expert_id,
            file_path=file_path,
            line_start=line_start,
        )
        tool_evidence = self.capability_service.collect_tool_evidence(expert, review.subject)
        active_skills = self.review_skill_activation_service.activate(
            expert,
            review.subject,
            analysis_mode,
            self.review_skill_registry.list_all(),
        )
        design_docs = self._review_design_docs(review.subject)
        runtime_tool_results = self.review_tool_gateway.invoke_for_expert(
            expert,
            review.subject,
            runtime_settings,
            file_path=file_path,
            line_start=line_start,
            related_files=list(related_files or []),
            design_docs=design_docs,
            extra_tools=self._collect_skill_tools(active_skills),
            active_skills=[str(skill.skill_id) for skill in active_skills if str(getattr(skill, "skill_id", "")).strip()],
        )
        MemoryProbe.log(
            "expert.after_runtime_tools",
            review_id=review.review_id,
            expert_id=expert.expert_id,
            runtime_tool_result_count=len(runtime_tool_results),
        )
        repository_context = self._merge_runtime_repository_context(
            dict(repository_context or {}),
            runtime_tool_results,
        )
        target_hunk = dict(target_hunk or {})
        target_hunks = [dict(item) for item in list(target_hunks or []) if isinstance(item, dict)]
        normalized_batch_items = self._normalize_expert_batch_items(
            batch_items,
            fallback_file_path=file_path,
            fallback_line_start=line_start,
            fallback_repository_context=repository_context,
            fallback_target_hunk=target_hunk,
            fallback_target_hunks=target_hunks,
            fallback_related_files=list(related_files or []),
        )
        repository_context = self._ensure_repository_context_minimum(
            review=review,
            repository_context=repository_context,
            batch_items=normalized_batch_items,
            fallback_file_path=file_path,
            fallback_line_start=line_start,
            fallback_target_hunk=target_hunk,
        )
        if target_hunk:
            repository_context["target_hunk"] = dict(target_hunk)
            hunk_excerpt = str(target_hunk.get("excerpt") or "").strip()
            if hunk_excerpt and not str(repository_context.get("target_hunk_excerpt") or "").strip():
                repository_context["target_hunk_excerpt"] = hunk_excerpt
        if target_hunks and not list(repository_context.get("target_hunks") or []):
            repository_context["target_hunks"] = [dict(item) for item in target_hunks if isinstance(item, dict)]
        multi_file_batch = len(normalized_batch_items) > 1
        if multi_file_batch:
            file_labels = [str(item.get("file_path") or "").strip() for item in normalized_batch_items if str(item.get("file_path") or "").strip()]
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_batch_scope",
                    content=f"{expert.name_zh} 本轮将批量审查 {len(file_labels)} 个文件，减少重复 LLM 往返。",
                    metadata={
                        "phase": "expert_review",
                        "batch_file_count": len(file_labels),
                        "batch_files": file_labels[:20],
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
        for tool_result in tool_evidence:
            tool_name = str(tool_result.get("tool_name") or "")
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_tool_call",
                    content=str(tool_result.get("summary") or f"{tool_name} 调用完成"),
                    metadata={
                        "phase": "expert_review",
                        "tool_name": tool_name,
                        "file_path": file_path,
                        "line_start": line_start,
                        "tool_result": self._build_tool_result_metadata(tool_result),
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_tool_invoked",
                    phase="expert_review",
                    message=f"{expert.name_zh} 调用了 tool {tool_name}",
                    payload={"expert_id": expert.expert_id, "tool_name": tool_name},
                )
            )
        for batch_message in self._build_rule_screening_batch_messages(
            review=review,
            expert=expert,
            file_path=file_path,
            line_start=line_start,
            rule_screening=rule_screening,
            runtime_settings=runtime_settings,
        ):
            self.message_repo.append(batch_message)
            batch_payload = dict(batch_message.metadata.get("rule_screening_batch") or {})
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_rule_screening_batch",
                    phase="coordination",
                    message=batch_message.content,
                    payload={
                        "expert_id": expert.expert_id,
                        "batch_index": int(batch_payload.get("batch_index") or 0),
                        "batch_count": int(batch_payload.get("batch_count") or 0),
                    },
                )
            )
        prompt_budget_metadata = self._build_expert_prompt_budget_metadata(
            subject=review.subject,
            expert=expert,
            file_path=file_path,
            line_start=line_start,
            runtime_tool_results=runtime_tool_results,
            repository_context=repository_context,
            target_hunk=target_hunk,
            target_hunks=target_hunks,
            bound_documents=bound_documents,
            active_skills=active_skills,
            rule_screening=rule_screening,
            analysis_mode=analysis_mode,
            include_target_file_full_diff=not multi_file_batch,
            include_related_diff_summary=not multi_file_batch,
        )
        prompt_request_budget = dict(prompt_budget_metadata.get("prompt_request_budget") or {})
        repository_context_budget = dict(prompt_budget_metadata.get("repository_context_budget") or {})
        if prompt_request_budget:
            logger.info(
                "expert prompt budget review_id=%s expert_id=%s file_path=%s line_start=%s used=%s total=%s kept_sections=%s dropped_context_blocks=%s",
                review.review_id,
                expert.expert_id,
                file_path,
                line_start,
                prompt_request_budget.get("used_budget"),
                prompt_request_budget.get("total_budget"),
                prompt_budget_metadata.get("retained_light_sections") or [],
                repository_context_budget.get("dropped_blocks") or [],
            )
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_ack",
                content=(
                    f"收到，{expert.name_zh} 将先检查 {file_path} 第 {line_start} 行附近的变更，"
                    f"重点验证 {expert.focus_areas[0] if expert.focus_areas else expert.role}。"
                ),
                metadata={
                    "phase": "coordination",
                    "reply_to_message_id": command_message.message_id,
                    "reply_to_expert_id": self.main_agent_service.agent_id,
                    "file_path": file_path,
                    "line_start": line_start,
                    "allowed_tools": expert.tool_bindings,
                    "allowed_runtime_tools": expert.runtime_tool_bindings,
                    "knowledge_sources": expert.knowledge_sources,
                    "active_skills": [skill.skill_id for skill in active_skills],
                    "bound_document_titles": [str(getattr(item, "title", "") or "") for item in bound_documents[:8]],
                    "bound_documents": self._build_bound_document_metadata(bound_documents),
                    "knowledge_context": self._build_knowledge_context_metadata(knowledge_context),
                    "rule_screening": self._build_rule_screening_metadata(rule_screening),
                    "related_files": list(related_files or []),
                    "business_changed_files": list(business_changed_files or []),
                    "target_hunk": target_hunk,
                    "repository_context": self._build_repository_context_metadata(repository_context),
                    "expected_checks": list(expected_checks or []),
                    "disallowed_inference": list(disallowed_inference or []),
                    "runtime_tool_results": self._build_runtime_tool_results_metadata(runtime_tool_results),
                    "design_doc_titles": self._normalize_text_list(
                        [item.get("title") for item in design_docs],
                        [],
                    ),
                    "prompt_budget": prompt_budget_metadata,
                    "target_hunks": target_hunks[:8],
                    **self._expert_llm_metadata(expert, runtime_settings),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="expert_started",
                phase="expert_review",
                message=f"{expert.name_zh} 收到主Agent指令后开始审查",
                payload={"expert_id": expert.expert_id, "file_path": file_path, "line_start": line_start},
            )
        )
        tool_messages: list[ConversationMessage] = []
        tool_events: list[ReviewEvent] = []
        for tool_result in runtime_tool_results:
            tool_name = str(tool_result.get("tool_name") or "")
            tool_messages.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_tool_call",
                    content=str(tool_result.get("summary") or f"{tool_name} 调用完成"),
                    metadata={
                        "phase": "expert_review",
                        "tool_name": tool_name,
                        "file_path": file_path,
                        "line_start": line_start,
                        "tool_result": self._build_tool_result_metadata(tool_result),
                        "tool_category": "runtime",
                        "target_hunk": target_hunk,
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
            tool_events.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_tool_invoked",
                    phase="expert_review",
                    message=f"{expert.name_zh} 调用了运行时工具 {tool_name}",
                    payload={"expert_id": expert.expert_id, "tool_name": tool_name, "tool_category": "runtime"},
                )
            )
        if tool_messages:
            self.message_repo.append_many(tool_messages)
        if tool_events:
            self.event_repo.append_many(tool_events)
        self._emit_skill_summary_messages(
            review=review,
            expert=expert,
            file_path=file_path,
            line_start=line_start,
            active_skills=active_skills,
            runtime_tool_results=runtime_tool_results,
            target_hunk=target_hunk,
            runtime_settings=runtime_settings,
        )
        self._abort_if_closed(review.review_id)

        base_severity, base_confidence = self._score_finding(review.subject, expert.expert_id)
        input_completeness = self._build_review_input_completeness(
            review.subject,
            file_path,
            line_start,
            repository_context,
            expert=expert,
            bound_documents=bound_documents or [],
            rule_screening=rule_screening or {},
            language=self._infer_code_language(file_path),
        )
        missing_required_context = self._extract_missing_required_context_sections(input_completeness)
        if missing_required_context:
            logger.info(
                "expert input completeness degraded review_id=%s expert_id=%s file_path=%s line_start=%s missing=%s source_context_present=%s related_context_count=%s target_diff_present=%s",
                review.review_id,
                expert.expert_id,
                file_path,
                line_start,
                missing_required_context,
                bool(input_completeness.get("source_context_present")),
                int(input_completeness.get("related_context_count") or 0),
                bool(input_completeness.get("target_file_diff_present")),
            )
        if missing_required_context:
            skip_message = (
                f"{expert.name_zh} 本轮上下文不完整：缺失 {' / '.join(missing_required_context[:4])}。"
                "系统将继续审查，但只允许输出基于当前证据可直接成立的问题。"
            )
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_context_warning",
                    content=skip_message,
                    metadata={
                        "phase": "expert_review",
                        "file_path": file_path,
                        "line_start": line_start,
                        "missing_required_context": missing_required_context,
                        "input_completeness": input_completeness,
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_context_warning",
                    phase="expert_review",
                    message=skip_message,
                    payload={
                        "expert_id": expert.expert_id,
                        "file_path": file_path,
                        "line_start": line_start,
                        "missing_required_context": missing_required_context,
                    },
                )
            )
        llm_resolution = self.llm_chat_service.resolve_expert(expert, runtime_settings)
        prompt_profile = resolve_model_prompt_profile(
            llm_resolution.model,
            profile_name=runtime_settings.review_prompt_profile,
        )
        effective_prompt_profile_name = prompt_profile.name
        user_prompt = self._build_expert_prompt(
            review.subject,
            expert,
            file_path,
            line_start,
            tool_evidence,
            runtime_tool_results,
            repository_context,
            target_hunk,
            target_hunks,
            bound_documents,
            list(disallowed_inference or []),
            list(expected_checks or []),
            active_skills,
            rule_screening,
            analysis_mode=analysis_mode,
            include_target_file_full_diff=not multi_file_batch,
            include_related_diff_summary=not multi_file_batch,
            model_name=llm_resolution.model,
            prompt_profile_name=effective_prompt_profile_name,
        )
        batch_hunk_count = self._count_batch_hunks(normalized_batch_items, fallback_target_hunks=target_hunks)
        max_findings_cap = min(80, max(8, len(normalized_batch_items) * 6, batch_hunk_count * 2))
        if multi_file_batch:
            if prompt_profile.require_rule_check_results:
                output_requirements = (
                    "输出补充要求：\n"
                    f"1. 本次允许输出最多 {max_findings_cap} 条 candidate_findings；\n"
                    "2. 输出根结构必须包含 rule_check_results、candidate_findings、context_requests、self_check；\n"
                    "3. 不允许输出 legacy {\"findings\":[...]}，不要输出单对象、不要输出 Markdown；\n"
                    "4. 每条 candidate_finding 必须包含 rule_id、title、file_path、line、evidence、confidence；\n"
                    "5. file_path 只能从“本轮批量文件清单”里选择；\n"
                    "6. 每条 candidate_finding 只能对应一个具体问题和一个主代码位置，禁止把多个文件、多个风险点、多个修复方向合并成一条；\n"
                    "7. 每条适用 RULE_CARDS 都必须在 rule_check_results 中给出 status、evidence、missing_context、reason；\n"
                    "8. self_check.checked_all_rules 必须反映是否已经逐条检查 RULE_CARDS。\n"
                )
            else:
                output_requirements = (
                    "输出补充要求：\n"
                    f"1. 本次允许输出最多 {max_findings_cap} 条 findings；\n"
                    "2. 输出根结构必须是 {\"findings\":[...]}，不要输出单对象、不要输出 Markdown；\n"
                    "3. 每条 finding 都必须同时包含 file_path、line_start、line_end、title、claim、suggested_code；\n"
                    "4. 每条 finding 必须携带 file_path，且只能从“本轮批量文件清单”里选择；\n"
                    "5. 每个文件允许返回多条互不重复的问题，不要只给每个文件 1 条；\n"
                    "6. 每条 finding 只能对应一个具体问题和一个主代码位置，禁止把多个文件、多个风险点、多个修复方向合并成一条；\n"
                    "7. suggested_code 必须是对应文件的具体修改后代码片段，不能写成修复思路、说明文字、占位符或伪代码。\n"
                )
            user_prompt = (
                f"{user_prompt}\n\n"
                f"{self._build_multi_file_prompt_appendix(review.subject, expert, normalized_batch_items, analysis_mode=analysis_mode)}\n"
                f"{output_requirements}"
            )
        required_rule_ids = self._rule_guided_required_rule_ids(
            rule_screening or {},
            max_rules_per_prompt=prompt_profile.max_rules_per_prompt,
        )
        rule_prepass_text = ""
        rule_prepass_metadata: dict[str, object] = {}
        should_run_rule_prepass = self._should_run_rule_guided_prepass(
            prompt_profile=prompt_profile,
            rule_screening=rule_screening or {},
            required_rule_ids=required_rule_ids,
        )
        if should_run_rule_prepass:
            rule_prepass_text, rule_prepass_metadata = self._run_rule_guided_rule_check_prepass(
                review=review,
                expert=expert,
                runtime_settings=runtime_settings,
                resolution=llm_resolution,
                base_user_prompt=user_prompt,
                required_rule_ids=required_rule_ids,
                rule_screening=rule_screening or {},
                normalized_batch_items=normalized_batch_items,
                repository_context=repository_context,
                file_path=file_path,
                line_start=line_start,
                timeout_seconds=float(llm_request_options["timeout_seconds"]),
            )
            if rule_prepass_text:
                user_prompt = (
                    f"{user_prompt}\n\n"
                    "[RULE_CHECK_PREPASS]\n"
                    f"{rule_prepass_text}\n\n"
                    "第二阶段任务：基于上述 rule_check_results 进行高召回 candidate_findings 发现；"
                    "仍必须在最终 JSON 中原样或修正后包含完整 rule_check_results。"
                )
                if self._should_short_circuit_rule_prepass_with_static_finding(
                    rule_prepass_text=rule_prepass_text,
                    required_rule_ids=required_rule_ids,
                    expert_id=expert.expert_id,
                    file_path=file_path,
                    target_hunk=target_hunk,
                    repository_context=repository_context,
                ):
                    self.message_repo.append(
                        ConversationMessage(
                            review_id=review.review_id,
                            issue_id="review_orchestration",
                            expert_id=expert.expert_id,
                            message_type="expert_rule_prepass_deterministic_finding",
                            content=(
                                f"{expert.name_zh} 第一阶段已命中结构化规则且静态证据充分，"
                                "系统跳过耗时主审查调用并直接生成确定性 finding。"
                            ),
                            metadata={
                                "phase": "expert_review",
                                "file_path": file_path,
                                "line_start": line_start,
                                "required_rule_ids": required_rule_ids,
                                "prompt_profile": effective_prompt_profile_name,
                                "rule_check_prepass": rule_prepass_metadata,
                                "target_hunk": target_hunk,
                                "repository_context": self._build_repository_context_metadata(repository_context),
                                **self._expert_llm_metadata(expert, runtime_settings),
                            },
                        )
                    )
                    if self._persist_deterministic_rule_signal_finding(
                        review=review,
                        expert=expert,
                        command_message=command_message,
                        file_path=file_path,
                        line_start=line_start,
                        repository_context=repository_context,
                        target_hunk=target_hunk,
                        target_hunks=target_hunks,
                        bound_documents=bound_documents,
                        rule_screening=rule_screening,
                        finding_payloads=finding_payloads,
                        reason="规则预检命中异常吞掉规则，且 diff/上下文包含 catch 删除 printStackTrace 的直接静态证据。",
                    ):
                        return
        expert_system_prompt = self._build_expert_system_prompt(
            expert,
            bound_documents,
            active_skills,
            rule_screening,
            analysis_mode=analysis_mode,
            model_name=llm_resolution.model,
            prompt_profile_name=effective_prompt_profile_name,
        )
        thorough_review_enabled = self._review_thorough_mode_enabled(runtime_settings, prompt_profile)
        fast_light_mode = (
            analysis_mode == "light"
            and str(os.getenv("REVIEW_LIGHT_EXTRA_LLM_SCANS", "") or "").strip().lower() not in {"1", "true", "yes"}
        )
        main_prompt_contract = self._build_prompt_contract_metadata(
            stage="expert_main_rule_guided_review" if prompt_profile.require_rule_check_results else "expert_main_legacy_review",
            system_prompt=expert_system_prompt,
            user_prompt=user_prompt,
            required_sections=(
                ["[SYSTEM RULES]", "[TASK]", "[EXPERT_PROFILE]", "[RULE_CARDS]", "[CONTEXT_PACKET]", "[OUTPUT_JSON]"]
                if prompt_profile.require_rule_check_results
                else []
            ),
            required_rule_ids=required_rule_ids,
            scope="main expert review: rule checks plus high-recall candidates",
        )
        try:
            llm_result = self.llm_chat_service.complete_text(
                system_prompt=expert_system_prompt,
                user_prompt=user_prompt,
                resolution=llm_resolution,
                runtime_settings=runtime_settings,
                fallback_text=self._build_expert_fallback(review.subject, expert, file_path, line_start),
                allow_fallback=self._allow_llm_fallback(runtime_settings),
                timeout_seconds=float(llm_request_options["timeout_seconds"]),
                max_attempts=int(llm_request_options["max_attempts"]),
                log_context={
                    "review_id": review.review_id,
                    "issue_id": "review_orchestration",
                    "expert_id": expert.expert_id,
                    "phase": "expert_review",
                    "analysis_mode": analysis_mode,
                    "file_path": file_path,
                    "line_start": line_start,
                    "prompt_budget": prompt_budget_metadata,
                    "prompt_contract": main_prompt_contract,
                },
            )
        except Exception as exc:
            if not prompt_profile.require_rule_check_results:
                raise
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_timeout_deterministic_fallback",
                    content=(
                        f"{expert.name_zh} 主审查调用失败，系统将基于已命中规则、上下文包和静态观察"
                        "生成确定性兜底 finding，避免弱模型或网络超时阻塞整轮检视。"
                    ),
                    metadata={
                        "phase": "expert_review",
                        "file_path": file_path,
                        "line_start": line_start,
                        "prompt_profile": effective_prompt_profile_name,
                        "timeout_error": str(exc),
                        "required_rule_ids": required_rule_ids,
                        "rule_screening": self._build_rule_screening_metadata(rule_screening),
                        "input_completeness": input_completeness,
                        "repository_context": self._build_repository_context_metadata(repository_context),
                        "target_hunk": target_hunk,
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
            self._record_expert_job_failure(
                {
                    "review": review,
                    "expert": expert,
                    "command_message": command_message,
                    "file_path": file_path,
                    "line_start": line_start,
                    "repository_context": repository_context,
                    "target_hunk": target_hunk,
                    "target_hunks": target_hunks,
                    "bound_documents": bound_documents,
                    "rule_screening": rule_screening,
                    "finding_payloads": finding_payloads,
                },
                exc,
            )
            return
        llm_text_for_parse = llm_result.text
        schema_contract_metadata: dict[str, object] = {}
        if prompt_profile.require_rule_check_results:
            contract_valid, contract_errors = self._validate_rule_guided_llm_response_contract(
                llm_text_for_parse,
                required_rule_ids=required_rule_ids,
            )
            if not contract_valid:
                repair_text, repair_metadata = self._repair_rule_guided_llm_response_schema(
                    review=review,
                    expert=expert,
                    runtime_settings=runtime_settings,
                    resolution=llm_resolution,
                    original_user_prompt=user_prompt,
                    original_response=llm_text_for_parse,
                    rule_screening=rule_screening or {},
                    required_rule_ids=required_rule_ids,
                    file_path=file_path,
                    line_start=line_start,
                    max_findings=max_findings_cap,
                    timeout_seconds=float(llm_request_options["timeout_seconds"]),
                )
                schema_contract_metadata = {
                    "initial_valid": False,
                    "initial_errors": contract_errors,
                    **repair_metadata,
                }
                repair_valid, repair_errors = self._validate_rule_guided_llm_response_contract(
                    repair_text,
                    required_rule_ids=required_rule_ids,
                )
                if repair_valid:
                    llm_text_for_parse = repair_text
                    contract_valid = True
                    contract_errors = []
                else:
                    contract_errors = repair_errors or contract_errors
            if contract_valid and not fast_light_mode:
                followup_text, followup_metadata = self._maybe_run_context_request_followup(
                    review=review,
                    expert=expert,
                    runtime_settings=runtime_settings,
                    resolution=llm_resolution,
                    original_user_prompt=user_prompt,
                    current_response=llm_text_for_parse,
                    required_rule_ids=required_rule_ids,
                    file_path=file_path,
                    line_start=line_start,
                    timeout_seconds=float(llm_request_options["timeout_seconds"]),
                )
                if followup_metadata:
                    schema_contract_metadata = {
                        **schema_contract_metadata,
                        "context_followup": followup_metadata,
                    }
                if followup_text:
                    llm_text_for_parse = followup_text
            if not contract_valid:
                self.message_repo.append(
                    ConversationMessage(
                        review_id=review.review_id,
                        issue_id="review_orchestration",
                        expert_id=expert.expert_id,
                        message_type="expert_schema_contract_failed",
                        content=(
                            f"{expert.name_zh} 未按规则驱动结构输出，系统已拒绝 legacy 专家结果并保留诊断。"
                        ),
                        metadata={
                            "phase": "expert_review",
                            "file_path": file_path,
                            "line_start": line_start,
                            "prompt_profile": effective_prompt_profile_name,
                            "schema_errors": contract_errors,
                            "schema_repair": schema_contract_metadata,
                            "raw_response_excerpt": self._clip_diagnostic_text(str(llm_result.text or ""), 1600),
                            **self._expert_llm_metadata(expert, runtime_settings),
                        },
                    )
                )
        MemoryProbe.log(
            "expert.after_llm",
            review_id=review.review_id,
            expert_id=expert.expert_id,
            llm_mode=llm_result.mode,
            llm_error=llm_result.error,
            prompt_budget_total=prompt_request_budget.get("total_budget"),
            prompt_budget_used=prompt_request_budget.get("used_budget"),
        )
        self._abort_if_closed(review.review_id)
        expert_llm_diagnostics = self._build_expert_llm_diagnostics(
            system_prompt=expert_system_prompt,
            user_prompt=user_prompt,
            llm_text=llm_text_for_parse,
            rule_screening=rule_screening,
            input_completeness=input_completeness,
            prompt_profile_name=effective_prompt_profile_name,
            prompt_contract=main_prompt_contract,
        )
        if schema_contract_metadata:
            expert_llm_diagnostics["schema_contract"] = schema_contract_metadata
        if rule_prepass_metadata:
            expert_llm_diagnostics["rule_check_prepass"] = rule_prepass_metadata
        parsed_candidates = self._parse_expert_analyses(
            llm_text_for_parse,
            review.subject,
            expert,
            file_path,
            line_start,
            max_findings=max_findings_cap,
            require_rule_guided=not prompt_profile.allow_legacy_expert_output,
        )
        empty_retry_metadata: dict[str, object] = {}
        if (
            not fast_light_mode
            and
            prompt_profile.require_rule_check_results
            and not parsed_candidates
            and self._should_retry_empty_rule_guided_candidate_response(
                target_hunk=target_hunk,
                target_hunks=target_hunks,
                repository_context=repository_context,
                rule_screening=rule_screening or {},
            )
        ):
            retry_text, empty_retry_metadata = self._run_rule_guided_empty_candidate_retry(
                review=review,
                expert=expert,
                runtime_settings=runtime_settings,
                resolution=llm_resolution,
                previous_response=llm_text_for_parse,
                rule_screening=rule_screening or {},
                required_rule_ids=required_rule_ids,
                normalized_batch_items=normalized_batch_items,
                repository_context=repository_context,
                file_path=file_path,
                line_start=line_start,
                timeout_seconds=float(llm_request_options["timeout_seconds"]),
            )
            if empty_retry_metadata:
                expert_llm_diagnostics["empty_candidate_retry"] = empty_retry_metadata
            if retry_text:
                retry_candidates = self._parse_expert_analyses(
                    retry_text,
                    review.subject,
                    expert,
                    file_path,
                    line_start,
                    max_findings=max_findings_cap,
                    require_rule_guided=True,
                )
                if retry_candidates:
                    parsed_candidates = self._merge_expert_analysis_candidates(
                        parsed_candidates,
                        retry_candidates,
                        max_findings=max_findings_cap,
                    )
        should_scan_review_targets = self._should_retry_empty_rule_guided_candidate_response(
            target_hunk=target_hunk,
            target_hunks=target_hunks,
            repository_context=repository_context,
            rule_screening=rule_screening or {},
        )
        if fast_light_mode:
            custom_scan_texts: list[str] = []
            custom_scan_metadata = {"attempted": False, "skipped_reason": "light_mode_fast_path"}
        else:
            custom_scan_texts, custom_scan_metadata = self._run_rule_guided_custom_rule_scan_batches(
                review=review,
                expert=expert,
                runtime_settings=runtime_settings,
                resolution=llm_resolution,
                rule_screening=rule_screening or {},
                normalized_batch_items=normalized_batch_items,
                repository_context=repository_context,
                max_rules_per_batch=prompt_profile.max_rules_per_prompt,
                file_path=file_path,
                line_start=line_start,
                timeout_seconds=float(llm_request_options["timeout_seconds"]),
            )
        if custom_scan_metadata:
            expert_llm_diagnostics["custom_rule_batch_scan"] = custom_scan_metadata
        for custom_scan_text in custom_scan_texts:
            custom_candidates = self._parse_expert_analyses(
                custom_scan_text,
                review.subject,
                expert,
                file_path,
                line_start,
                max_findings=max_findings_cap,
                require_rule_guided=True,
            )
            if custom_candidates:
                parsed_candidates = self._merge_expert_analysis_candidates(
                    parsed_candidates,
                    custom_candidates,
                    max_findings=max_findings_cap,
                )
        general_scan_metadata: dict[str, object] = {}
        if (
            prompt_profile.require_rule_check_results
            and not fast_light_mode
            and should_scan_review_targets
            and (thorough_review_enabled or not parsed_candidates)
        ):
            general_scan_text, general_scan_metadata = self._run_rule_guided_general_expert_profile_scan(
                review=review,
                expert=expert,
                runtime_settings=runtime_settings,
                resolution=llm_resolution,
                normalized_batch_items=normalized_batch_items,
                repository_context=repository_context,
                file_path=file_path,
                line_start=line_start,
                timeout_seconds=float(llm_request_options["timeout_seconds"]),
            )
            if general_scan_metadata:
                expert_llm_diagnostics["general_expert_profile_scan"] = general_scan_metadata
            if general_scan_text:
                general_candidates = self._parse_expert_analyses(
                    general_scan_text,
                    review.subject,
                    expert,
                    file_path,
                    line_start,
                    max_findings=max_findings_cap,
                    require_rule_guided=True,
                )
                if general_candidates:
                    parsed_candidates = self._merge_expert_analysis_candidates(
                        parsed_candidates,
                        general_candidates,
                        max_findings=max_findings_cap,
                    )
        parsed_candidates = self._append_observation_followup_candidates(
            review=review,
            subject=review.subject,
            expert=expert,
            file_path=file_path,
            line_start=line_start,
            repository_context=repository_context,
            normalized_batch_items=normalized_batch_items,
            runtime_settings=runtime_settings,
            analysis_mode=analysis_mode,
            llm_request_options=llm_request_options,
            bound_documents=bound_documents,
            active_skills=active_skills,
            rule_screening=rule_screening,
            initial_candidates=parsed_candidates,
            max_findings=max_findings_cap,
        )
        if (
            thorough_review_enabled
            and prompt_profile.require_rule_check_results
            and should_scan_review_targets
            and not parsed_candidates
        ):
            zero_gate_metadata = {
                "phase": "expert_review",
                "review_quality_mode": "thorough_review",
                "file_path": file_path,
                "line_start": line_start,
                "prompt_profile": effective_prompt_profile_name,
                "main_candidate_count": int((expert_llm_diagnostics.get("rule_coverage") or {}).get("candidate_count") or 0),
                "empty_retry_attempted": bool(empty_retry_metadata.get("attempted")),
                "general_scan_attempted": bool(general_scan_metadata.get("attempted")),
                "custom_scan_attempted": bool(custom_scan_metadata.get("attempted")),
                "custom_scan_batch_count": int(custom_scan_metadata.get("batch_count") or 0),
                "prompt_contract": main_prompt_contract,
                "general_scan": general_scan_metadata,
                "custom_rule_batch_scan": custom_scan_metadata,
                "empty_candidate_retry": empty_retry_metadata,
                "input_completeness": input_completeness,
                **self._expert_llm_metadata(expert, runtime_settings),
            }
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_zero_candidate_quality_gate",
                    content=(
                        f"{expert.name_zh} 深度检视已完成主审查、通用扫描和绑定规范扫描，"
                        "但本轮仍未产出候选问题；系统保留该质量门禁诊断用于回放分析。"
                    ),
                    metadata=zero_gate_metadata,
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_zero_candidate_quality_gate",
                    phase="expert_review",
                    message=f"{expert.name_zh} 深度检视零候选质量门禁已记录",
                    payload={
                        "expert_id": expert.expert_id,
                        "file_path": file_path,
                        "line_start": line_start,
                        "general_scan_attempted": bool(general_scan_metadata.get("attempted")),
                        "custom_scan_attempted": bool(custom_scan_metadata.get("attempted")),
                    },
                )
            )
        design_alignment = self._extract_design_alignment(runtime_tool_results)
        saved_count = 0
        pending_findings: list[ReviewFinding] = []
        pending_analysis_messages: list[ConversationMessage] = []
        dedupe_keys: set[tuple[str, int, str, str]] = set()
        used_hunk_lines_by_file: dict[str, set[int]] = {}
        candidate_count = len(parsed_candidates)
        for index, raw_parsed in enumerate(parsed_candidates, start=1):
            finding_file_path = self._resolve_finding_file_path(
                raw_parsed,
                fallback_file_path=file_path,
                batch_items=normalized_batch_items,
            )
            if not finding_file_path:
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="finding_dropped_unresolved_file",
                        phase="expert_review",
                        message=f"{expert.name_zh} 返回的 finding 未能定位到具体文件，已丢弃以避免串线。",
                        payload={
                            "expert_id": expert.expert_id,
                            "candidate_index": index,
                            "title": str(raw_parsed.get("title") or "").strip(),
                            "line_start": int(raw_parsed.get("line_start") or 0) if raw_parsed.get("line_start") else 0,
                            "batch_file_count": len(normalized_batch_items),
                        },
                    )
                )
                continue
            per_file_batch_item = self._find_batch_item_for_file(normalized_batch_items, finding_file_path)
            per_file_target_hunk = dict(
                (per_file_batch_item or {}).get("target_hunk") or target_hunk
            )
            per_file_target_hunks = [
                dict(item)
                for item in list((per_file_batch_item or {}).get("target_hunks") or [])
                if isinstance(item, dict)
            ]
            if not per_file_target_hunks:
                per_file_target_hunks = [
                    dict(item)
                    for item in target_hunks
                    if str(item.get("file_path") or finding_file_path).strip() == finding_file_path
                ] or list(target_hunks)
            file_used_lines = used_hunk_lines_by_file.setdefault(finding_file_path, set())
            per_file_repository_context = dict(
                (per_file_batch_item or {}).get("repository_context") or repository_context or {}
            )
            matched_target_hunk = self._resolve_finding_target_hunk(
                raw_parsed,
                fallback_line_start=line_start,
                target_hunk=per_file_target_hunk,
                target_hunks=per_file_target_hunks,
                used_hunk_line_starts=file_used_lines,
            )
            matched_hunk_line_start = self._target_hunk_anchor_line(
                matched_target_hunk,
                fallback=int((per_file_batch_item or {}).get("line_start") or line_start or 1),
            )
            file_used_lines.add(int(matched_hunk_line_start or line_start or 1))
            raw_candidate_line_start = self._normalize_optional_line_value(
                raw_parsed.get("line_start") or raw_parsed.get("line")
            )
            if (
                raw_candidate_line_start is not None
                and int(raw_candidate_line_start) == int(line_start or 1)
                and int(matched_hunk_line_start or line_start or 1) != int(line_start or 1)
                and len(per_file_target_hunks) > 1
            ):
                raw_parsed = {
                    **raw_parsed,
                    "line_start": int(matched_hunk_line_start or line_start or 1),
                    "line_end": int(matched_hunk_line_start or line_start or 1),
                }
            parsed = self._stabilize_expert_analysis(
                raw_parsed,
                expert.expert_id,
                finding_file_path,
                matched_hunk_line_start,
                matched_target_hunk,
                repository_context=per_file_repository_context,
                input_completeness=input_completeness,
            )
            if bool(parsed.get("schema_rejected")):
                continue
            suggested_code = str(parsed.get("suggested_code") or "").strip()
            if not self._looks_like_concrete_suggested_code(suggested_code, file_path=finding_file_path):
                suggested_code = self._repair_missing_suggested_code(
                    review=review,
                    expert=expert,
                    runtime_settings=runtime_settings,
                    llm_request_options=llm_request_options,
                    file_path=finding_file_path,
                    line_start=int(parsed.get("line_start") or matched_hunk_line_start or line_start or 1),
                    parsed=parsed,
                    target_hunk=matched_target_hunk,
                )
                parsed["suggested_code"] = suggested_code
            confidence = self._normalize_confidence(parsed.get("confidence"), base_confidence)
            parsed_line_start = self._normalize_line_start(parsed.get("line_start"), matched_hunk_line_start)
            parsed_line_start = self._refine_line_start_within_hunk(parsed, matched_target_hunk, parsed_line_start)
            parsed_line_start = self._refine_line_start_across_hunks(
                parsed,
                per_file_target_hunks,
                parsed_line_start,
            )
            parsed = self._normalize_candidate_for_refined_anchor(
                parsed,
                expert_id=expert.expert_id,
                line_start=parsed_line_start,
            )
            parsed_line_start = self._refine_line_start_across_hunks(
                parsed,
                per_file_target_hunks,
                parsed_line_start,
            )
            if not self._line_in_target_hunks(parsed_line_start, per_file_target_hunks):
                parsed_line_start = int(matched_hunk_line_start or parsed_line_start or 1)
            if not self._finding_has_valid_diff_anchor(
                review.subject,
                finding_file_path,
                parsed_line_start,
                matched_target_hunk,
            ):
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="finding_dropped_outside_diff",
                        phase="expert_review",
                        message=f"{expert.name_zh} 返回的 finding 未锚定到 MR 变更后的 diff 行，已丢弃。",
                        payload={
                            "expert_id": expert.expert_id,
                            "candidate_index": index,
                            "file_path": finding_file_path,
                            "line_start": parsed_line_start,
                            "title": str(parsed.get("title") or "").strip(),
                            "target_hunk_changed_lines": self._normalize_changed_line_values(
                                matched_target_hunk.get("changed_lines")
                            ),
                        },
                    )
                )
                continue
            current_diff_match = self._finding_matches_current_diff_code(parsed, matched_target_hunk)
            if not bool(current_diff_match.get("matched", True)):
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="finding_dropped_stale_code",
                        phase="expert_review",
                        message=f"{expert.name_zh} 返回的 finding 疑似基于 MR 修改前代码，已丢弃。",
                        payload={
                            "expert_id": expert.expert_id,
                            "candidate_index": index,
                            "file_path": finding_file_path,
                            "line_start": parsed_line_start,
                            "title": str(parsed.get("title") or "").strip(),
                            "removed_token_overlap": list(current_diff_match.get("removed_token_overlap") or [])[:8],
                            "added_token_overlap": list(current_diff_match.get("added_token_overlap") or [])[:8],
                            "added_lines": list(current_diff_match.get("added_lines") or [])[:6],
                            "removed_lines": list(current_diff_match.get("removed_lines") or [])[:6],
                        },
                    )
                )
                continue
            rule_attribution = self._normalize_finding_rule_attribution(
                parsed,
                rule_screening=rule_screening,
                expert_id=expert.expert_id,
            )
            parsed["matched_rules"] = list(rule_attribution.get("normalized_matched_rules") or [])
            parsed["assumptions"] = self._dedupe_texts(
                [
                    *[str(item).strip() for item in list(parsed.get("assumptions") or []) if str(item).strip()],
                    *[str(item).strip() for item in list(rule_attribution.get("assumptions") or []) if str(item).strip()],
                ]
            )
            if list(rule_attribution.get("invalid_custom_rule_ids") or []):
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="finding_rule_attribution_corrected",
                        phase="expert_review",
                        message=f"{expert.name_zh} 返回的 finding 引用了本轮未提供的附加规则，系统已清理该规则引用。",
                        payload={
                            "expert_id": expert.expert_id,
                            "candidate_index": index,
                            "file_path": finding_file_path,
                            "line_start": parsed_line_start,
                            "title": str(parsed.get("title") or "").strip(),
                            "invalid_custom_rule_ids": list(rule_attribution.get("invalid_custom_rule_ids") or [])[:8],
                        },
                    )
                )
            candidate_verification = self._verify_rule_guided_candidate_before_persist(
                parsed=parsed,
                finding_file_path=finding_file_path,
                parsed_line_start=parsed_line_start,
                rule_screening=rule_screening,
                repository_context=per_file_repository_context,
                input_completeness=input_completeness,
            )
            if candidate_verification and candidate_verification.get("status") == "rejected":
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="rule_guided_candidate_rejected",
                        phase="expert_review",
                        message=f"{expert.name_zh} 的规则驱动候选未通过落库前校验，已丢弃。",
                        payload={
                            "expert_id": expert.expert_id,
                            "candidate_index": index,
                            "file_path": finding_file_path,
                            "line_start": parsed_line_start,
                            "title": str(parsed.get("title") or "").strip(),
                            "verification": candidate_verification,
                        },
                    )
                )
                continue
            if candidate_verification and candidate_verification.get("status") == "needs_context":
                parsed["verification_needed"] = True
                missing_context_values = [
                    str(item).strip()
                    for item in list(candidate_verification.get("missing_context") or [])
                    if str(item).strip()
                ]
                if missing_context_values:
                    parsed["verification_plan"] = (
                        str(parsed.get("verification_plan") or "").strip()
                        or "复核问题位置、规则证据和建议代码是否一致；不一致时降级为候选发现。"
                    )
                    parsed["assumptions"] = self._dedupe_texts(
                        [
                            *[
                                str(item).strip()
                                for item in list(parsed.get("assumptions") or [])
                                if str(item).strip()
                            ],
                            *[f"缺失上下文: {item}" for item in missing_context_values],
                        ]
                    )
            severity = self._apply_additive_rule_priority_to_severity(
                self._normalize_severity(parsed.get("severity"), base_severity),
                finding_type=str(parsed.get("finding_type") or "risk_hypothesis"),
                rule_attribution=rule_attribution,
            )
            dedupe_key = (
                str(parsed.get("title") or "").strip().lower(),
                parsed_line_start,
                str(parsed.get("finding_type") or "risk_hypothesis").strip().lower(),
                str(parsed.get("claim") or "").strip().lower(),
            )
            if dedupe_key in dedupe_keys:
                continue
            dedupe_keys.add(dedupe_key)

            finding = ReviewFinding(
                review_id=review.review_id,
                expert_id=expert.expert_id,
                title=str(parsed.get("title") or self._build_finding_title(expert)),
                summary=str(parsed.get("claim") or self._build_finding_summary(review.subject, expert.expert_id)),
                finding_type=str(parsed.get("finding_type") or "risk_hypothesis"),
                normalized_issue_type=self._normalize_issue_type(parsed, expert.expert_id),
                severity=severity,
                confidence=confidence,
                file_path=finding_file_path,
                line_start=parsed_line_start,
                evidence=self._build_evidence(review.subject, expert, finding_file_path, tool_evidence, parsed),
                cross_file_evidence=[str(item).strip() for item in parsed.get("cross_file_evidence", []) if str(item).strip()],
                assumptions=[str(item).strip() for item in parsed.get("assumptions", []) if str(item).strip()],
                context_files=self._merge_context_files(
                    parsed.get("context_files", []),
                    per_file_repository_context,
                    runtime_tool_results,
                ),
                matched_rules=self._normalize_text_list(parsed.get("matched_rules"), []),
                violated_guidelines=self._normalize_text_list(parsed.get("violated_guidelines"), []),
                rule_based_reasoning=str(parsed.get("rule_based_reasoning") or "").strip(),
                verification_needed=bool(parsed.get("verification_needed", parsed.get("needs_verification", False))),
                verification_plan=str(parsed.get("verification_plan") or "").strip(),
                design_alignment_status=str(parsed.get("design_alignment_status") or design_alignment.get("design_alignment_status") or "").strip(),
                design_doc_titles=self._normalize_text_list(
                    design_alignment.get("design_doc_titles"),
                    [],
                ),
                matched_design_points=self._normalize_text_list(
                    parsed.get("matched_design_points"),
                    self._normalize_text_list(design_alignment.get("matched_implementation_points"), []),
                ),
                missing_design_points=self._normalize_text_list(
                    parsed.get("missing_design_points"),
                    self._normalize_text_list(design_alignment.get("missing_implementation_points"), []),
                ),
                extra_implementation_points=self._normalize_text_list(
                    parsed.get("extra_implementation_points"),
                    self._normalize_text_list(design_alignment.get("extra_implementation_points"), []),
                ),
                design_conflicts=self._normalize_text_list(
                    parsed.get("design_conflicts"),
                    self._normalize_text_list(design_alignment.get("conflicting_implementation_points"), []),
                ),
                remediation_strategy=str(
                    parsed.get("fix_strategy")
                    or self._build_remediation_strategy(review.subject, expert.expert_id, finding_file_path)
                ),
                remediation_suggestion=str(
                    parsed.get("suggested_fix")
                    or self._build_remediation_suggestion(review.subject, expert.expert_id, finding_file_path)
                ),
                remediation_steps=self._normalize_text_list(
                    parsed.get("change_steps"),
                    self._build_remediation_steps(review.subject, expert.expert_id, finding_file_path),
                ),
                code_excerpt=self._build_code_excerpt(
                    review.subject,
                    finding_file_path,
                    parsed_line_start,
                    expert.expert_id,
                ),
                code_context=self._build_finding_code_context(
                    review.subject,
                    finding_file_path,
                    parsed_line_start,
                    matched_target_hunk,
                    per_file_repository_context,
                    expert=expert,
                    bound_documents=bound_documents,
                    rule_screening=rule_screening,
                ),
                suggested_code=str(parsed.get("suggested_code") or "").strip(),
                suggested_code_language=self._infer_code_language(finding_file_path),
            )
            sast_matches = self._match_sast_prescan_findings(
                parsed=parsed,
                file_path=finding_file_path,
                line_start=parsed_line_start,
                repository_context=per_file_repository_context,
            )
            if sast_matches:
                code_context = dict(finding.code_context or {})
                code_context["sast_prescan_matches"] = sast_matches
                code_context["sast_cross_validated"] = True
                finding.code_context = code_context
                finding.evidence = self._dedupe_texts(
                    [
                        *finding.evidence,
                        *[
                            formatted
                            for formatted in (self._format_sast_prescan_evidence(item) for item in sast_matches)
                            if formatted
                        ],
                    ]
                )
                finding.matched_rules = self._dedupe_texts(
                    [
                        *finding.matched_rules,
                        *[
                            str(item.get("rule_id") or "").strip()
                            for item in sast_matches
                            if str(item.get("rule_id") or "").strip()
                        ],
                    ]
                )
            observation_ids = self._normalize_text_list(parsed.get("observation_ids"), [])
            if observation_ids:
                code_context = dict(finding.code_context or {})
                code_context["observation_ids"] = observation_ids
                finding.code_context = code_context
            if rule_attribution:
                code_context = dict(finding.code_context or {})
                code_context["rule_attribution"] = rule_attribution
                finding.code_context = code_context
            if candidate_verification:
                code_context = dict(finding.code_context or {})
                code_context["candidate_verification"] = candidate_verification
                finding.code_context = code_context
            finding.context_source = self._finding_context_source(finding.code_context)
            finding.evidence_chain = self._build_finding_evidence_chain(finding)
            if not finding.category_label:
                finding.category_label = self._category_label_for_finding(
                    finding_type=finding.finding_type,
                    issue_type=finding.normalized_issue_type,
                    expert_id=finding.expert_id,
                )
            if not finding.confidence_rationale:
                finding.confidence_rationale = self._confidence_rationale_for_finding(
                    confidence=finding.confidence,
                    finding_type=finding.finding_type,
                    evidence=finding.evidence,
                    matched_rules=finding.matched_rules,
                    verification_needed=finding.verification_needed,
                    code_context=finding.code_context,
                )
            if self._should_skip_finding(expert.expert_id, finding):
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="finding_suppressed",
                        phase="expert_review",
                        message=f"{expert.name_zh} 的低证据发现已被抑制",
                        payload={
                            "expert_id": expert.expert_id,
                            "file_path": finding.file_path,
                            "line_start": finding.line_start,
                            "finding_type": finding.finding_type,
                        },
                    )
                )
                continue
            self._abort_if_closed(review.review_id)
            pending_findings.append(finding)
            message_content = (
                llm_result.text.strip()
                if index == 1
                else f"[multi-finding {index}/{candidate_count}] {finding.title}\n{finding.summary}"
            )
            pending_analysis_messages.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id=finding.finding_id,
                    expert_id=expert.expert_id,
                    message_type="expert_analysis",
                    content=message_content,
                    metadata={
                        "phase": "expert_review",
                        "severity": finding.severity,
                        "confidence": finding.confidence,
                        "file_path": finding.file_path,
                        "line_start": finding.line_start,
                        "reply_to_expert_id": self.main_agent_service.agent_id,
                        "reply_to_message_id": command_message.message_id,
                        "target_expert_id": expert.expert_id,
                        "allowed_tools": expert.tool_bindings,
                        "allowed_runtime_tools": expert.runtime_tool_bindings,
                        "knowledge_sources": expert.knowledge_sources,
                        "active_skills": [skill.skill_id for skill in active_skills],
                        "tool_evidence": [self._build_tool_result_metadata(item) for item in tool_evidence[:6]],
                        "runtime_tool_results": self._build_runtime_tool_results_metadata(runtime_tool_results),
                        "target_hunk": matched_target_hunk,
                        "target_hunks": per_file_target_hunks[:8],
                        "repository_context": self._build_repository_context_metadata(per_file_repository_context),
                        "bound_document_titles": [str(getattr(item, "title", "") or "") for item in bound_documents[:8]],
                        "bound_documents": self._build_bound_document_metadata(bound_documents),
                        "knowledge_context": self._build_knowledge_context_metadata(knowledge_context),
                        "rule_screening": self._build_rule_screening_metadata(rule_screening),
                        "rule_attribution": finding.code_context.get("rule_attribution", {}),
                        "candidate_verification": finding.code_context.get("candidate_verification", {}),
                        "prompt_profile": effective_prompt_profile_name,
                        **expert_llm_diagnostics,
                        "finding_type": finding.finding_type,
                        "context_files": finding.context_files,
                        "assumptions": finding.assumptions,
                        "matched_rules": finding.matched_rules,
                        "violated_guidelines": finding.violated_guidelines,
                        "rule_based_reasoning": finding.rule_based_reasoning,
                        "design_alignment_status": finding.design_alignment_status,
                        "design_doc_titles": finding.design_doc_titles,
                        "matched_design_points": finding.matched_design_points,
                        "missing_design_points": finding.missing_design_points,
                        "extra_implementation_points": finding.extra_implementation_points,
                        "design_conflicts": finding.design_conflicts,
                        "analysis_mode": analysis_mode,
                        "multi_finding_index": index,
                        "multi_finding_total": candidate_count,
                        "batch_file_count": len(normalized_batch_items),
                        "input_completeness": finding.code_context.get("input_completeness", {}),
                        "review_inputs": finding.code_context.get("review_inputs", {}),
                        "prompt_budget": prompt_budget_metadata,
                        **self._llm_message_metadata(llm_result),
                    },
                )
            )
            finding_payloads.append(finding.model_dump(mode="json"))
            saved_count += 1
        if saved_count <= 0:
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_final",
                    content=llm_result.text.strip() or f"{expert.name_zh} 未形成可落库 finding。",
                    metadata={
                        "phase": "expert_review",
                        "file_path": file_path,
                        "line_start": line_start,
                        "reply_to_expert_id": self.main_agent_service.agent_id,
                        "reply_to_message_id": command_message.message_id,
                        "target_expert_id": expert.expert_id,
                        "rule_screening": self._build_rule_screening_metadata(rule_screening),
                        "input_completeness": input_completeness,
                        "prompt_profile": effective_prompt_profile_name,
                        **expert_llm_diagnostics,
                        **self._llm_message_metadata(llm_result),
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_no_persisted_findings",
                    phase="expert_review",
                    message=f"{expert.name_zh} 完成模型分析，但本轮未形成可落库 finding。",
                    payload={
                        "expert_id": expert.expert_id,
                        "file_path": file_path,
                        "line_start": line_start,
                        "candidate_count": candidate_count,
                        "context_gaps": list(expert_llm_diagnostics.get("context_gaps") or []),
                    },
                )
            )
            return
        self.finding_repo.save_many(review.review_id, pending_findings)
        for item in pending_findings:
            MemoryProbe.log(
                "expert.after_finding_save",
                review_id=review.review_id,
                expert_id=expert.expert_id,
                finding_id=item.finding_id,
            )
        if pending_analysis_messages:
            self.message_repo.append_many(pending_analysis_messages)
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="finding_created",
                phase="expert_review",
                message=f"{expert.name_zh} 生成审核发现",
                payload={
                    "expert_id": expert.expert_id,
                    "finding_count": len(pending_findings),
                    "finding_ids": [item.finding_id for item in pending_findings[:12]],
                },
            )
        )

    def _persist_issue_thread(
        self,
        *,
        review: ReviewTask,
        issue: DebateIssue,
        experts_by_id: dict[str, ExpertProfile],
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
        llm_request_options: dict[str, int | float],
    ) -> None:
        self._abort_if_closed(review.review_id)
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="debate_issue_created",
                phase="debate",
                message=f"{issue.title} 已进入议题池",
                payload={"issue_id": issue.issue_id, "status": issue.status},
            )
        )
        max_debate_rounds = max(1, int(runtime_settings.default_max_debate_rounds or 1))
        debate_participants = issue.participant_expert_ids[:max_debate_rounds] or ["correctness_business", "ddd_architecture"]
        debate_participants = [item for item in debate_participants if item in experts_by_id] or list(experts_by_id)[:2]
        debate_participants = debate_participants[:max_debate_rounds]
        previous_expert_id = self.main_agent_service.agent_id
        issue_file_path = issue.file_path or self._pick_file_path(review.subject, debate_participants[0] if debate_participants else "correctness_business")
        issue_line_start = issue.line_start or self.diff_excerpt_service.find_nearest_line(
            review.subject.unified_diff,
            issue_file_path,
            1,
        ) or 1
        light_debate_fast_path = (
            analysis_mode == "light"
            and str(os.getenv("REVIEW_LIGHT_DEBATE_LLM", "") or "").strip().lower()
            not in {"1", "true", "yes"}
        )
        if issue.needs_debate and debate_participants and light_debate_fast_path:
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="debate_skipped",
                    phase="debate",
                    message=f"{issue.title} 在轻量模式下跳过额外辩论 LLM，沿用裁决后的结构化结论",
                    payload={
                        "issue_id": issue.issue_id,
                        "participants": debate_participants,
                        "analysis_mode": analysis_mode,
                        "skipped_reason": "light_mode_fast_path",
                    },
                )
            )
        elif issue.needs_debate and debate_participants:
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="debate_started",
                    phase="debate",
                    message=f"{issue.title} 开始定向辩论",
                    payload={"issue_id": issue.issue_id, "participants": debate_participants},
                )
            )
            for index, participant_id in enumerate(debate_participants):
                self._abort_if_closed(review.review_id)
                expert = experts_by_id.get(participant_id)
                if expert is None:
                    continue
                file_path = issue_file_path
                line_start = issue_line_start
                knowledge_context = self._build_knowledge_review_context(
                    review.subject,
                    expert,
                    file_path,
                    line_start,
                    {},
                    {},
                )
                bound_documents = self.knowledge_service.retrieve_for_expert(
                    expert.expert_id,
                    knowledge_context,
                )
                llm_result = self.llm_chat_service.complete_text(
                    system_prompt=self._build_expert_system_prompt(
                        expert,
                        bound_documents,
                        [],
                        analysis_mode=analysis_mode,
                    ),
                    user_prompt=self._build_debate_prompt(
                        review.subject,
                        issue,
                        expert,
                        previous_expert_id,
                        file_path,
                        line_start,
                        bound_documents,
                    ),
                    resolution=self.llm_chat_service.resolve_expert(expert, runtime_settings),
                    runtime_settings=runtime_settings,
                    fallback_text=self._build_debate_fallback(
                        issue,
                        expert,
                        previous_expert_id,
                        file_path,
                        line_start,
                    ),
                    allow_fallback=self._allow_llm_fallback(runtime_settings),
                    timeout_seconds=float(llm_request_options["timeout_seconds"]),
                    max_attempts=int(llm_request_options["max_attempts"]),
                    log_context={
                        "review_id": review.review_id,
                        "issue_id": issue.issue_id,
                        "expert_id": participant_id,
                        "phase": "debate",
                        "analysis_mode": analysis_mode,
                        "file_path": file_path,
                        "line_start": line_start,
                        "debate_turn": index + 1,
                    },
                )
                self.message_repo.append(
                    ConversationMessage(
                        review_id=review.review_id,
                        issue_id=issue.issue_id,
                        expert_id=participant_id,
                        message_type="debate_message",
                        content=llm_result.text.strip(),
                        metadata={
                            "phase": "debate",
                            "issue_status": issue.status,
                            "resolution": issue.resolution,
                            "file_path": file_path,
                            "line_start": line_start,
                            "reply_to_expert_id": previous_expert_id,
                            "bound_document_titles": [str(getattr(item, "title", "") or "") for item in bound_documents[:8]],
                            "bound_documents": self._build_bound_document_metadata(bound_documents),
                            "knowledge_context": self._build_knowledge_context_metadata(knowledge_context),
                            "debate_turn": index + 1,
                            "analysis_mode": analysis_mode,
                            **self._llm_message_metadata(llm_result),
                        },
                    )
                )
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="debate_message",
                        phase="debate",
                        message=f"{expert.name_zh} 提交了辩论意见",
                        payload={"issue_id": issue.issue_id, "expert_id": participant_id},
                    )
                )
                previous_expert_id = participant_id

        self._abort_if_closed(review.review_id)
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id=issue.issue_id,
                expert_id="judge",
                message_type="judge_summary",
                content=issue.summary,
                metadata={
                    "phase": "judge",
                    "status": issue.status,
                    "needs_human": issue.needs_human,
                    "verified": issue.verified,
                    "file_path": issue_file_path,
                    "line_start": issue_line_start,
                    "reply_to_expert_id": previous_expert_id,
                },
            )
        )
    def _expert_llm_metadata(self, expert: ExpertProfile, runtime_settings) -> dict[str, object]:
        resolution = self.llm_chat_service.resolve_expert(expert, runtime_settings)
        return {
            "provider": resolution.provider,
            "model": resolution.model,
            "base_url": resolution.base_url,
            "api_key_env": resolution.api_key_env,
            "mode": "pending",
        }

    def _review_thorough_mode_enabled(self, runtime_settings, prompt_profile) -> bool:
        """Return whether independent high-recall scans should run for every rule-guided expert."""

        if getattr(prompt_profile, "allow_legacy_expert_output", False):
            return False
        mode = str(getattr(runtime_settings, "review_quality_mode", "") or "").strip().lower()
        return mode == "thorough_review"

    def _build_prompt_contract_metadata(
        self,
        *,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        required_sections: list[str],
        forbidden_sections: list[str] | None = None,
        required_rule_ids: list[str] | None = None,
        scope: str = "",
    ) -> dict[str, object]:
        """Build lightweight prompt lint diagnostics for replay and weak-model debugging."""

        user_text = str(user_prompt or "")
        system_text = str(system_prompt or "")
        missing_sections = [section for section in required_sections if section and section not in user_text]
        forbidden_hits = [section for section in list(forbidden_sections or []) if section and section in user_text]
        conflicts: list[str] = []
        lowered_user = user_text.lower()
        lowered_system = system_text.lower()
        if stage == "expert_general_profile_scan":
            if "[custom_rule_batch]" in lowered_user or "custom_rule_batch" in lowered_user:
                conflicts.append("general_scan_contains_custom_rule_batch")
            if "只按本批 custom_rule_batch" in lowered_system:
                conflicts.append("general_system_scope_conflicts_with_custom_batch")
        if stage == "expert_custom_rule_batch_scan":
            if "rule_id 使用 general-expert-checks" in lowered_user.lower():
                conflicts.append("custom_scan_allows_general_rule_id")
            if "语言通用规范" in lowered_system and "custom_rule_batch" not in lowered_system:
                conflicts.append("custom_system_scope_may_include_general_rules")
        if stage == "expert_rule_check_prepass" and "candidate_findings 必须输出空数组" not in user_text:
            conflicts.append("rule_prepass_missing_empty_candidate_instruction")
        return {
            "stage": stage,
            "scope": scope,
            "valid": not missing_sections and not forbidden_hits and not conflicts,
            "required_sections": list(required_sections),
            "missing_sections": missing_sections,
            "forbidden_sections": list(forbidden_sections or []),
            "forbidden_hits": forbidden_hits,
            "conflicts": conflicts,
            "required_rule_ids": [str(item).strip() for item in list(required_rule_ids or []) if str(item).strip()],
            "system_prompt_chars": len(system_text),
            "user_prompt_chars": len(user_text),
        }

    def _llm_message_metadata(self, llm_result) -> dict[str, object]:
        metadata = {
            "llm_call_id": llm_result.call_id,
            "provider": llm_result.provider,
            "model": llm_result.model,
            "base_url": llm_result.base_url,
            "api_key_env": llm_result.api_key_env,
            "mode": llm_result.mode,
            "llm_error": llm_result.error,
            "prompt_tokens": llm_result.prompt_tokens,
            "completion_tokens": llm_result.completion_tokens,
            "total_tokens": llm_result.total_tokens,
        }
        trace = {
            "system_prompt_snapshot_full": str(getattr(llm_result, "system_prompt_snapshot_full", "") or ""),
            "prompt_snapshot_full": str(getattr(llm_result, "prompt_snapshot_full", "") or ""),
            "model_raw_response_full": str(getattr(llm_result, "model_raw_response_full", "") or llm_result.text or ""),
        }
        if any(trace.values()):
            metadata["llm_trace"] = trace
        return metadata

    def _attach_environment_preflight(self, review: ReviewTask, runtime_settings) -> ReviewTask:
        preflight = run_review_environment_preflight(review.subject, runtime_settings)
        metadata = dict(review.subject.metadata or {})
        metadata["review_environment_preflight"] = preflight
        updated = review.model_copy(update={"subject": review.subject.model_copy(update={"metadata": metadata})})
        updated.updated_at = datetime.now(UTC)
        self.review_repo.save(updated)
        status = str(preflight.get("status") or "").strip()
        degraded_context_reasons = [
            str(item).strip()
            for item in list(preflight.get("degraded_context_reasons") or [])
            if str(item).strip()
        ]
        if status and status != "passed":
            message = "检视环境预检发现降级项：" + " / ".join(degraded_context_reasons or ["unknown"])
            self.message_repo.append(
                ConversationMessage(
                    review_id=updated.review_id,
                    issue_id="review_orchestration",
                    expert_id=self.main_agent_service.agent_id,
                    message_type="review_environment_preflight",
                    content=message,
                    metadata={
                        "phase": "intake",
                        "environment_status": status,
                        "degraded_context_reasons": degraded_context_reasons,
                        "path_resolution_failures": list(preflight.get("path_resolution_failures") or []),
                        "normalized_changed_files": list(preflight.get("normalized_changed_files") or []),
                        "code_graph_db_path": preflight.get("code_graph_db_path"),
                        "code_graph_db_exists": preflight.get("code_graph_db_exists"),
                        "tree_sitter_graph_result": preflight.get("tree_sitter_graph_result"),
                        "gitnexus_graph_result": preflight.get("gitnexus_graph_result"),
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=updated.review_id,
                    event_type="review_environment_preflight_warning",
                    phase="intake",
                    message=message,
                    payload=preflight,
                )
            )
        return updated

    def _build_expert_llm_diagnostics(
        self,
        *,
        system_prompt: str = "",
        user_prompt: str,
        llm_text: str,
        rule_screening: dict[str, object],
        input_completeness: dict[str, object],
        prompt_profile_name: str,
        prompt_contract: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = self._parse_json_payload(llm_text)
        rule_check_results: list[dict[str, object]] = []
        candidate_findings: list[dict[str, object]] = []
        context_requests: list[dict[str, object]] = []
        self_check: dict[str, object] = {}
        if isinstance(payload, dict):
            rule_check_results = [
                dict(item)
                for item in list(payload.get("rule_check_results") or [])
                if isinstance(item, dict)
            ][:30]
            candidate_findings = [
                dict(item)
                for item in list(payload.get("candidate_findings") or [])
                if isinstance(item, dict)
            ][:30]
            context_requests = [
                dict(item)
                for item in list(payload.get("context_requests") or [])
                if isinstance(item, dict)
            ][:30]
            if isinstance(payload.get("self_check"), dict):
                self_check = dict(payload.get("self_check") or {})
        context_gaps = self._dedupe_texts(
            [
                *self._extract_missing_required_context_sections(input_completeness),
                *[
                    str(item).strip()
                    for result in rule_check_results
                    for item in list(result.get("missing_context") or [])
                    if str(item).strip()
                ],
                *[
                    str(request.get("missing_context") or "").strip()
                    for request in context_requests
                    if str(request.get("missing_context") or "").strip()
                ],
            ]
        )
        return {
            "prompt_snapshot_summary": {
                "profile": str(prompt_profile_name or "auto"),
                "system_prompt_chars": len(str(system_prompt or "")),
                "prompt_chars": len(str(user_prompt or "")),
                "contains_system_rules": "[SYSTEM RULES]" in str(user_prompt or ""),
                "contains_rule_cards": "[RULE_CARDS]" in str(user_prompt or ""),
                "contains_context_packet": "[CONTEXT_PACKET]" in str(user_prompt or ""),
                "contains_output_json": "[OUTPUT_JSON]" in str(user_prompt or ""),
                "prompt_contract_valid": bool((prompt_contract or {}).get("valid", True)),
            },
            "system_prompt_snapshot_full": self._clip_diagnostic_text(str(system_prompt or ""), 120000),
            "prompt_snapshot_full": self._clip_diagnostic_text(str(user_prompt or ""), 180000),
            "prompt_contract": dict(prompt_contract or {}),
            "model_raw_response_excerpt": self._clip_diagnostic_text(str(llm_text or ""), 4000),
            "model_raw_response_full": self._clip_diagnostic_text(str(llm_text or ""), 180000),
            "rule_check_results": rule_check_results,
            "candidate_findings": candidate_findings,
            "context_requests": context_requests,
            "self_check": self_check,
            "context_gaps": context_gaps,
            "rule_coverage": {
                "matched_rule_count": int((rule_screening or {}).get("matched_rule_count") or 0),
                "checked_rule_count": len(rule_check_results),
                "candidate_count": len(candidate_findings),
                "insufficient_context_count": sum(
                    1
                    for item in rule_check_results
                    if str(item.get("status") or "").strip().lower() == "insufficient_context"
                ),
            },
        }

    def _build_rule_guided_timeout_recovery_prompt(
        self,
        *,
        base_user_prompt: str,
        rule_screening: dict[str, object],
        required_rule_ids: list[str],
        normalized_batch_items: list[dict[str, object]],
        repository_context: dict[str, object],
        file_path: str,
        line_start: int,
        timeout_error: str,
    ) -> str:
        rules = [
            dict(item)
            for item in list((rule_screening or {}).get("matched_rules_for_llm") or [])
            if isinstance(item, dict)
        ]
        targets: list[dict[str, object]] = []
        for item in list(normalized_batch_items or [])[:8]:
            if not isinstance(item, dict):
                continue
            target_hunk = dict(item.get("target_hunk") or {})
            item_repository_context = (
                dict(item.get("repository_context") or {})
                if isinstance(item.get("repository_context"), dict)
                else {}
            )
            targets.append(
                {
                    "file_path": str(item.get("file_path") or target_hunk.get("file_path") or "").strip(),
                    "line_start": int(item.get("line_start") or target_hunk.get("line_start") or 1),
                    "target_hunk": self._clip_diagnostic_text(str(target_hunk.get("content") or item.get("hunk") or ""), 1800),
                    "context_summary": self._clip_diagnostic_text(
                        str(self._build_repository_context_metadata(item_repository_context) or ""),
                        1800,
                    ),
                }
            )
        if not targets:
            targets.append({"file_path": file_path, "line_start": line_start})
        compact_context = self._build_repository_context_metadata(repository_context or {})
        return "\n".join(
            [
                "[TIMEOUT_RECOVERY_RULE_GUIDED_REVIEW]",
                "上一次专家主审查 LLM 调用超时。请用更短流程完成高召回审查，禁止输出 Markdown。",
                "只基于下方 RULE_CARDS、TARGETS、COMPACT_CONTEXT、ORIGINAL_PROMPT_EXCERPT 输出 JSON。",
                "如果证据不足但存在可疑代码证据，candidate_findings 仍保留，同时 context_requests 写明缺什么。",
                f"timeout_error: {self._clip_diagnostic_text(str(timeout_error or ''), 500)}",
                f"REQUIRED_RULE_IDS: {json.dumps(required_rule_ids or ['GENERAL-EXPERT-CHECKS'], ensure_ascii=False)}",
                "",
                "[RULE_CARDS]",
                json.dumps(rules or [{"rule_id": "GENERAL-EXPERT-CHECKS", "title": "专家通用必查项"}], ensure_ascii=False, indent=2)[:12000],
                "",
                "[TARGETS]",
                json.dumps(targets, ensure_ascii=False, indent=2),
                "",
                "[COMPACT_CONTEXT]",
                self._clip_diagnostic_text(json.dumps(compact_context, ensure_ascii=False, indent=2), 8000),
                "",
                "[OUTPUT_JSON]",
                json.dumps(
                    {
                        "rule_check_results": [
                            {
                                "rule_id": "string",
                                "status": "violated|passed|not_applicable|insufficient_context",
                                "evidence": ["string"],
                                "missing_context": ["string"],
                                "reason": "string",
                            }
                        ],
                        "candidate_findings": [
                            {
                                "rule_id": "string",
                                "title": "string",
                                "target_id": "必须从 TARGET_HUNKS[].target_id 原样选择",
                                "file_path": "必须从 TARGET_HUNKS[].file_path 原样选择",
                                "line": "必须取对应 hunk changed_lines 中的当前代码行号",
                                "evidence": "string",
                                "confidence": "high|medium|low",
                            }
                        ],
                        "context_requests": [],
                        "self_check": {
                            "checked_all_rules": True,
                            "used_context_files": [],
                            "unverified_assumptions": [],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
                "[ORIGINAL_PROMPT_EXCERPT]",
                self._clip_diagnostic_text(str(base_user_prompt or ""), 9000),
            ]
        )

    def _rule_guided_required_rule_ids(
        self,
        rule_screening: dict[str, object],
        *,
        max_rules_per_prompt: int,
    ) -> list[str]:
        ids: list[str] = []
        for item in list((rule_screening or {}).get("matched_rules_for_llm") or []):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or item.get("id") or "").strip()
            if rule_id and rule_id not in ids:
                ids.append(rule_id)
            if len(ids) >= max(1, int(max_rules_per_prompt or 1)):
                break
        if not ids:
            ids.append("GENERAL-EXPERT-CHECKS")
        return ids

    @staticmethod
    def _should_run_rule_guided_prepass(
        *,
        prompt_profile,
        rule_screening: dict[str, object],
        required_rule_ids: list[str],
    ) -> bool:
        if not bool(getattr(prompt_profile, "use_two_pass_review", False)):
            return False
        if not bool(getattr(prompt_profile, "require_rule_check_results", False)):
            return False
        real_rule_ids = [
            str(item.get("rule_id") or item.get("id") or "").strip()
            for item in list((rule_screening or {}).get("matched_rules_for_llm") or [])
            if isinstance(item, dict) and str(item.get("rule_id") or item.get("id") or "").strip()
        ]
        if real_rule_ids:
            return True
        # GENERAL-EXPERT-CHECKS is only a fallback output contract. A separate
        # LLM prepass for it is costly and can make weaker models over-request
        # full-file/schema context instead of judging concrete changed hunks.
        return any(str(rule_id or "").strip() != "GENERAL-EXPERT-CHECKS" for rule_id in required_rule_ids)

    def _run_rule_guided_rule_check_prepass(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        runtime_settings,
        resolution,
        base_user_prompt: str,
        required_rule_ids: list[str],
        rule_screening: dict[str, object],
        normalized_batch_items: list[dict[str, object]],
        repository_context: dict[str, object],
        file_path: str,
        line_start: int,
        timeout_seconds: float,
    ) -> tuple[str, dict[str, object]]:
        targets = self._build_empty_candidate_retry_targets(
            normalized_batch_items=normalized_batch_items,
            fallback_file_path=file_path,
            fallback_line_start=line_start,
        )
        prepass_prompt = "\n".join(
            [
                "[RULE_CHECK_PREPASS_ONLY]",
                "第一阶段：只做规则逐条检查，不要输出代码问题结论。",
                "你必须基于 RULE_CARDS、TARGET_HUNKS、COMPACT_CONTEXT 判断每条 REQUIRED_RULE_IDS 的状态。",
                "candidate_findings 必须输出空数组；只允许输出 rule_check_results、context_requests、self_check。",
                "状态只能是 violated、passed、not_applicable、insufficient_context。",
                f"REQUIRED_RULE_IDS: {json.dumps(required_rule_ids, ensure_ascii=False)}",
                "",
                "[RULE_CARDS]",
                self._build_rule_guided_rule_cards(
                    rule_screening or {},
                    required_rule_ids or ["GENERAL-EXPERT-CHECKS"],
                    max_rules_per_prompt=max(1, len(required_rule_ids or []), 8),
                ),
                "",
                "[TARGET_HUNKS]",
                json.dumps(targets, ensure_ascii=False, indent=2),
                "",
                "[COMPACT_CONTEXT]",
                self._clip_diagnostic_text(
                    self._build_repository_context_summary(repository_context or {}, []),
                    10000,
                ),
                "",
                "[PREPASS_SOURCE_NOTE]",
                f"原主审 prompt 长度: {len(str(base_user_prompt or ''))} 字符；本阶段不嵌套原主审 prompt，避免输出合同冲突。",
            ]
        )
        system_prompt = (
            "你是代码审查规则检查器。只输出 JSON，不输出 Markdown。"
            "本阶段禁止输出 candidate finding。"
        )
        prompt_contract = self._build_prompt_contract_metadata(
            stage="expert_rule_check_prepass",
            system_prompt=system_prompt,
            user_prompt=prepass_prompt,
            required_sections=["[RULE_CHECK_PREPASS_ONLY]", "[RULE_CARDS]", "[TARGET_HUNKS]", "[COMPACT_CONTEXT]"],
            forbidden_sections=["[EMPTY_CANDIDATE_RETRY]", "[GENERAL_EXPERT_PROFILE_REVIEW_ONLY]", "[CUSTOM_RULE_BATCH]"],
            required_rule_ids=required_rule_ids,
            scope="rule checks only; candidate_findings must stay empty",
        )
        try:
            result = self.llm_chat_service.complete_text(
                system_prompt=system_prompt,
                user_prompt=prepass_prompt,
                resolution=resolution,
                runtime_settings=runtime_settings,
                fallback_text=(
                    '{"rule_check_results":[],"candidate_findings":[],"context_requests":[]'
                    ',"self_check":{"checked_all_rules":false,"used_context_files":[]'
                    ',"unverified_assumptions":["rule prepass fallback"]}}'
                ),
                allow_fallback=False,
                timeout_seconds=max(20.0, min(float(timeout_seconds or 60), 60.0)),
                max_attempts=1,
                log_context={
                    "review_id": review.review_id,
                    "issue_id": "review_orchestration",
                    "expert_id": expert.expert_id,
                    "phase": "expert_rule_check_prepass",
                    "file_path": file_path,
                    "line_start": line_start,
                    "prompt_contract": prompt_contract,
                },
            )
        except Exception as exc:  # noqa: BLE001 - prepass failure should degrade, not abort.
            metadata = {"attempted": True, "success": False, "error": str(exc)}
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_rule_check_prepass",
                    content=f"{expert.name_zh} 第一阶段规则检查失败，后续将继续执行主审查并保留诊断。",
                    metadata={
                        "phase": "expert_review",
                        "file_path": file_path,
                        "line_start": line_start,
                        "rule_check_prepass": metadata,
                    },
                )
            )
            return "", metadata
        valid, errors = self._validate_rule_guided_llm_response_contract(
            result.text,
            required_rule_ids=required_rule_ids,
        )
        metadata = {
            "attempted": True,
            "success": valid,
            "schema_errors": errors,
            "required_rule_ids": required_rule_ids,
            "llm_call_id": result.call_id,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "prompt_snapshot_full": self._clip_diagnostic_text(prepass_prompt, 120000),
            "raw_response_excerpt": self._clip_diagnostic_text(str(result.text or ""), 1600),
            "raw_response_full": self._clip_diagnostic_text(str(result.text or ""), 120000),
            "prompt_contract": prompt_contract,
        }
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_rule_check_prepass",
                content=(
                    f"{expert.name_zh} 已完成第一阶段规则逐条检查。"
                    if valid
                    else f"{expert.name_zh} 第一阶段规则检查输出未满足结构合同，主审查会继续并保留诊断。"
                ),
                metadata={
                    "phase": "expert_review",
                    "file_path": file_path,
                    "line_start": line_start,
                    "rule_check_prepass": metadata,
                    "prompt_snapshot_full": metadata["prompt_snapshot_full"],
                    "model_raw_response_excerpt": metadata["raw_response_excerpt"],
                    "model_raw_response_full": metadata["raw_response_full"],
                    "schema_errors": errors,
                    **self._llm_message_metadata(result),
                },
            )
        )
        return (result.text if valid else ""), metadata

    def _should_retry_empty_rule_guided_candidate_response(
        self,
        *,
        target_hunk: dict[str, object],
        target_hunks: list[dict[str, object]],
        repository_context: dict[str, object],
        rule_screening: dict[str, object],
    ) -> bool:
        if self._has_substantive_changed_code(target_hunk):
            return True
        if any(self._has_substantive_changed_code(dict(item)) for item in list(target_hunks or []) if isinstance(item, dict)):
            return True
        if list((repository_context or {}).get("review_observations") or []):
            return True
        if list((rule_screening or {}).get("matched_rules_for_llm") or []):
            return True
        return False

    def _has_substantive_changed_code(self, target_hunk: dict[str, object]) -> bool:
        excerpt = str((target_hunk or {}).get("excerpt") or "")
        if not excerpt.strip():
            return False
        for raw_line in excerpt.splitlines():
            line = raw_line.strip()
            if not line.startswith("+") or line.startswith("+++"):
                continue
            code = line[1:].strip()
            if not code or code in {"{", "}", ");", "};"}:
                continue
            if code.startswith(("//", "*", "/*")):
                continue
            return True
        return False

    def _run_rule_guided_empty_candidate_retry(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        runtime_settings,
        resolution,
        previous_response: str,
        rule_screening: dict[str, object],
        required_rule_ids: list[str],
        normalized_batch_items: list[dict[str, object]],
        repository_context: dict[str, object],
        file_path: str,
        line_start: int,
        timeout_seconds: float,
    ) -> tuple[str, dict[str, object]]:
        targets = self._build_empty_candidate_retry_targets(
            normalized_batch_items=normalized_batch_items,
            fallback_file_path=file_path,
            fallback_line_start=line_start,
        )
        retry_prompt = "\n".join(
            [
                "[EMPTY_CANDIDATE_RETRY]",
                "上一轮输出结构合法，但 candidate_findings 为空。当前输入包含实质变更代码，因此必须重新做一次证据聚焦审查。",
                "本阶段不要复述上一轮结论，不要输出 Markdown。",
                "如果任一目标 hunk 中存在违反专家职责、专家规范、语言通用规范或 RULE_CARDS 的代码证据，必须输出 candidate_findings。",
                "每条 candidate_finding 只能描述一个具体问题、一个主文件和一个主代码位置；同一 hunk 的多个问题必须拆成多条。",
                "如果仍判断无问题，candidate_findings 可以为空，但 self_check.unverified_assumptions 必须写明逐个 hunk 无问题的具体原因。",
                "有当前变更代码位置但还缺上下文时，不要静默省略；保留 candidate_findings，并在 context_requests 说明缺什么。",
                f"专家: {expert.expert_id} / {expert.name_zh}",
                f"职责: {expert.role}",
                f"REQUIRED_RULE_IDS: {json.dumps(required_rule_ids or ['GENERAL-EXPERT-CHECKS'], ensure_ascii=False)}",
                "",
                "[RULE_CARDS]",
                self._build_rule_guided_rule_cards(
                    rule_screening or {},
                    [str(item).strip() for item in list(getattr(expert, "focus_areas", []) or []) if str(item).strip()],
                    max_rules_per_prompt=24,
                ),
                "",
                "[TARGET_HUNKS]",
                json.dumps(targets, ensure_ascii=False, indent=2),
                "",
                "[COMPACT_CONTEXT]",
                self._clip_diagnostic_text(
                    self._build_repository_context_summary(repository_context or {}, []),
                    12000,
                ),
                "",
                "[PREVIOUS_EMPTY_RESPONSE]",
                self._clip_diagnostic_text(str(previous_response or ""), 6000),
                "",
                "[OUTPUT_JSON]",
                json.dumps(
                    {
                        "rule_check_results": [
                            {
                                "rule_id": "string",
                                "status": "violated|passed|not_applicable|insufficient_context",
                                "evidence": ["string"],
                                "missing_context": ["string"],
                                "reason": "string",
                            }
                        ],
                        "candidate_findings": [
                            {
                                "rule_id": "string",
                                "title": "string",
                                "file_path": file_path,
                                "line": line_start,
                                "evidence": "string",
                                "confidence": "high|medium|low",
                            }
                        ],
                        "context_requests": [],
                        "self_check": {
                            "checked_all_rules": True,
                            "used_context_files": [],
                            "unverified_assumptions": [],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            ]
        )
        system_prompt = (
            "你是代码审查空结果复核器。上一轮为空不是成功结论；"
            "必须重新扫描目标 hunk，有代码证据就输出候选。只输出 JSON。"
        )
        prompt_contract = self._build_prompt_contract_metadata(
            stage="expert_empty_candidate_retry",
            system_prompt=system_prompt,
            user_prompt=retry_prompt,
            required_sections=["[EMPTY_CANDIDATE_RETRY]", "[RULE_CARDS]", "[TARGET_HUNKS]", "[COMPACT_CONTEXT]", "[OUTPUT_JSON]"],
            forbidden_sections=["[CUSTOM_BOUND_RULE_REVIEW_ONLY]"],
            required_rule_ids=required_rule_ids or ["GENERAL-EXPERT-CHECKS"],
            scope="empty-result challenge; rescan all target hunks",
        )
        try:
            result = self.llm_chat_service.complete_text(
                system_prompt=system_prompt,
                user_prompt=retry_prompt,
                resolution=resolution,
                runtime_settings=runtime_settings,
                fallback_text='{"rule_check_results":[],"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":false,"used_context_files":[],"unverified_assumptions":["empty candidate retry fallback"]}}',
                allow_fallback=False,
                timeout_seconds=max(20.0, min(float(timeout_seconds or 60), 75.0)),
                max_attempts=1,
                log_context={
                    "review_id": review.review_id,
                    "issue_id": "review_orchestration",
                    "expert_id": expert.expert_id,
                    "phase": "expert_empty_candidate_retry",
                    "file_path": file_path,
                    "line_start": line_start,
                    "prompt_contract": prompt_contract,
                },
            )
        except Exception as exc:  # noqa: BLE001 - empty retry is diagnostic and should not abort.
            metadata = {"attempted": True, "success": False, "error": str(exc)}
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_empty_candidate_retry",
                    content=f"{expert.name_zh} 空候选复核失败，系统将继续执行 observation 兜底。",
                    metadata={
                        "phase": "expert_review",
                        "file_path": file_path,
                        "line_start": line_start,
                        "empty_candidate_retry": metadata,
                    },
                )
            )
            return "", metadata
        valid, errors = self._validate_rule_guided_llm_response_contract(
            result.text,
            required_rule_ids=required_rule_ids or ["GENERAL-EXPERT-CHECKS"],
        )
        payload = self._parse_json_payload(result.text)
        candidate_count = (
            len([item for item in list(payload.get("candidate_findings") or []) if isinstance(item, dict)])
            if isinstance(payload, dict)
            else 0
        )
        metadata = {
            "attempted": True,
            "success": valid,
            "schema_errors": errors,
            "candidate_count": candidate_count,
            "required_rule_ids": required_rule_ids,
            "llm_call_id": result.call_id,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "prompt_snapshot_full": self._clip_diagnostic_text(retry_prompt, 120000),
            "raw_response_excerpt": self._clip_diagnostic_text(str(result.text or ""), 1600),
            "raw_response_full": self._clip_diagnostic_text(str(result.text or ""), 120000),
            "prompt_contract": prompt_contract,
        }
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_empty_candidate_retry",
                content=(
                    f"{expert.name_zh} 已完成空候选结果复核，补充 {candidate_count} 条候选。"
                    if candidate_count
                    else f"{expert.name_zh} 已完成空候选结果复核，仍未补充候选。"
                ),
                metadata={
                    "phase": "expert_review",
                    "file_path": file_path,
                    "line_start": line_start,
                    "empty_candidate_retry": metadata,
                    "prompt_snapshot_full": metadata["prompt_snapshot_full"],
                    "model_raw_response_excerpt": metadata["raw_response_excerpt"],
                    "model_raw_response_full": metadata["raw_response_full"],
                    "schema_errors": errors,
                    **self._llm_message_metadata(result),
                },
            )
        )
        return (result.text if valid else ""), metadata

    def _build_empty_candidate_retry_targets(
        self,
        *,
        normalized_batch_items: list[dict[str, object]],
        fallback_file_path: str,
        fallback_line_start: int,
    ) -> list[dict[str, object]]:
        targets: list[dict[str, object]] = []
        for item in list(normalized_batch_items or [])[:12]:
            if not isinstance(item, dict):
                continue
            file_path = str(item.get("file_path") or fallback_file_path or "").strip()
            target_hunks = [
                dict(hunk)
                for hunk in list(item.get("target_hunks") or [])
                if isinstance(hunk, dict)
            ] or ([dict(item.get("target_hunk") or {})] if isinstance(item.get("target_hunk"), dict) else [])
            for hunk in target_hunks[:8]:
                if not hunk:
                    continue
                target_id = f"T{len(targets) + 1}"
                targets.append(
                    {
                        "target_id": target_id,
                        "file_path": file_path,
                        "line_start": int(hunk.get("start_line") or hunk.get("line_start") or item.get("line_start") or fallback_line_start or 1),
                        "hunk_header": str(hunk.get("hunk_header") or ""),
                        "changed_lines": [
                            int(value)
                            for value in list(hunk.get("changed_lines") or [])
                            if isinstance(value, int)
                        ],
                        "current_added_lines": self._extract_current_added_lines_from_hunk(hunk),
                        "excerpt": self._clip_diagnostic_text(str(hunk.get("excerpt") or ""), 4000),
                    }
                )
        if not targets:
            targets.append({"target_id": "T1", "file_path": fallback_file_path, "line_start": int(fallback_line_start or 1)})
        return targets

    def _extract_current_added_lines_from_hunk(self, hunk: dict[str, object]) -> list[str]:
        """只把 MR 当前新增行单独暴露给弱模型，降低它引用删除代码的概率。"""

        excerpt = str((hunk or {}).get("excerpt") or "")
        added_lines: list[str] = []
        for raw_line in excerpt.splitlines():
            line = raw_line.strip()
            if "| +" in line:
                added_lines.append(line)
            elif line.startswith("+") and not line.startswith("+++"):
                added_lines.append(line)
        return added_lines[:24]

    def _run_rule_guided_general_expert_profile_scan(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        runtime_settings,
        resolution,
        normalized_batch_items: list[dict[str, object]],
        repository_context: dict[str, object],
        file_path: str,
        line_start: int,
        timeout_seconds: float,
    ) -> tuple[str, dict[str, object]]:
        targets = self._build_empty_candidate_retry_targets(
            normalized_batch_items=normalized_batch_items,
            fallback_file_path=file_path,
            fallback_line_start=line_start,
        )
        language = self._infer_code_language(file_path)
        prompt = "\n".join(
            [
                "[GENERAL_EXPERT_PROFILE_REVIEW_ONLY]",
                "本阶段只按专家画像、专家职责、专家审视规范和代码语言通用规范做通用检视。",
                "它和专家绑定规范扫描相互补充，最终候选取并集并由收敛层去重。",
                "如果存在当前变更代码位置和专家职责范围内的真实风险，必须输出 candidate_findings，rule_id 使用 GENERAL-EXPERT-CHECKS。",
                "如果缺少关联上下文但已有当前代码证据，不要静默省略；保留 candidate_findings，并在 context_requests 说明缺什么。",
                "每条 candidate_finding 只能描述一个具体问题、一个主文件和一个主代码位置；title、evidence、reason、suggested_code 必须互相指向同一问题。",
                "candidate_finding 的 target_id、file_path、line 必须来自 TARGET_HUNKS；禁止照抄 OUTPUT_JSON 的占位说明，禁止使用不在 TARGET_HUNKS 中的文件。",
                "不要输出专家绑定规范结论，不要编造产品规则 ID。",
                f"专家: {expert.expert_id} / {expert.name_zh}",
                f"专家画像:\n{self._compact_prompt_block(str(expert.system_prompt or expert.role or ''), 1800)}",
                f"专家审视规范:\n{self._compact_prompt_block(self._build_review_spec_summary(str(expert.review_spec or '')), 2200)}",
                f"代码语言: {language or 'unknown'}",
                f"语言通用规范:\n{self._compact_prompt_block(self._build_expert_language_general_guidance(language, expert.expert_id), 1600)}",
                "REQUIRED_RULE_IDS: [\"GENERAL-EXPERT-CHECKS\"]",
                "",
                "[TARGET_HUNKS]",
                json.dumps(targets, ensure_ascii=False, indent=2),
                "",
                "[COMPACT_CONTEXT]",
                self._clip_diagnostic_text(
                    self._build_repository_context_summary(repository_context or {}, []),
                    12000,
                ),
                "",
                "[OUTPUT_JSON]",
                json.dumps(
                    {
                        "rule_check_results": [
                            {
                                "rule_id": "GENERAL-EXPERT-CHECKS",
                                "status": "violated|passed|not_applicable|insufficient_context",
                                "evidence": ["string"],
                                "missing_context": ["string"],
                                "reason": "string",
                            }
                        ],
                        "candidate_findings": [
                            {
                                "rule_id": "GENERAL-EXPERT-CHECKS",
                                "title": "string",
                                "target_id": "必须从 TARGET_HUNKS[].target_id 原样选择",
                                "file_path": "必须从 TARGET_HUNKS[].file_path 原样选择",
                                "line": "必须取对应 hunk changed_lines 中的当前代码行号",
                                "evidence": "string",
                                "confidence": "high|medium|low",
                            }
                        ],
                        "context_requests": [],
                        "self_check": {
                            "checked_all_rules": True,
                            "used_context_files": [],
                            "unverified_assumptions": [],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "只输出一个 JSON 对象，不要输出 Markdown，不要添加额外解释。",
            ]
        )
        system_prompt = (
            "你是专家画像通用代码检视器。只按专家职责和语言通用规范扫描目标 hunk；"
            "不要编造绑定规则 ID，候选会和绑定规范扫描结果取并集。只输出 JSON。"
        )
        prompt_contract = self._build_prompt_contract_metadata(
            stage="expert_general_profile_scan",
            system_prompt=system_prompt,
            user_prompt=prompt,
            required_sections=["[GENERAL_EXPERT_PROFILE_REVIEW_ONLY]", "[TARGET_HUNKS]", "[COMPACT_CONTEXT]", "[OUTPUT_JSON]"],
            forbidden_sections=["[CUSTOM_RULE_BATCH]"],
            required_rule_ids=["GENERAL-EXPERT-CHECKS"],
            scope="general expert profile and language-guideline scan",
        )
        try:
            result = self.llm_chat_service.complete_text(
                system_prompt=system_prompt,
                user_prompt=prompt,
                resolution=resolution,
                runtime_settings=runtime_settings,
                fallback_text='{"rule_check_results":[],"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":false,"used_context_files":[],"unverified_assumptions":["general expert profile scan fallback"]}}',
                allow_fallback=False,
                timeout_seconds=max(20.0, min(float(timeout_seconds or 60), 75.0)),
                max_attempts=1,
                log_context={
                    "review_id": review.review_id,
                    "issue_id": "review_orchestration",
                    "expert_id": expert.expert_id,
                    "phase": "expert_general_profile_scan",
                    "file_path": file_path,
                    "line_start": line_start,
                    "prompt_contract": prompt_contract,
                },
            )
        except Exception as exc:  # noqa: BLE001 - general scan should degrade, not abort.
            metadata = {"attempted": True, "success": False, "error": str(exc)}
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_general_profile_scan",
                    content=f"{expert.name_zh} 专家画像通用扫描失败，系统将继续执行后续流程。",
                    metadata={
                        "phase": "expert_review",
                        "file_path": file_path,
                        "line_start": line_start,
                        "general_expert_profile_scan": metadata,
                    },
                )
            )
            return "", metadata
        valid, errors = self._validate_rule_guided_llm_response_contract(
            result.text,
            required_rule_ids=["GENERAL-EXPERT-CHECKS"],
        )
        payload = self._parse_json_payload(result.text)
        candidate_count = (
            len([item for item in list(payload.get("candidate_findings") or []) if isinstance(item, dict)])
            if isinstance(payload, dict)
            else 0
        )
        metadata = {
            "attempted": True,
            "success": valid,
            "schema_errors": errors,
            "candidate_count": candidate_count,
            "required_rule_ids": ["GENERAL-EXPERT-CHECKS"],
            "llm_call_id": result.call_id,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "prompt_snapshot_full": self._clip_diagnostic_text(prompt, 120000),
            "raw_response_excerpt": self._clip_diagnostic_text(str(result.text or ""), 1600),
            "raw_response_full": self._clip_diagnostic_text(str(result.text or ""), 120000),
            "prompt_contract": prompt_contract,
        }
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_general_profile_scan",
                content=(
                    f"{expert.name_zh} 已完成专家画像通用扫描，补充 {candidate_count} 条候选。"
                    if valid
                    else f"{expert.name_zh} 专家画像通用扫描输出未满足结构合同。"
                ),
                metadata={
                    "phase": "expert_review",
                    "file_path": file_path,
                    "line_start": line_start,
                    "general_expert_profile_scan": metadata,
                    "prompt_snapshot_full": metadata["prompt_snapshot_full"],
                    "model_raw_response_excerpt": metadata["raw_response_excerpt"],
                    "model_raw_response_full": metadata["raw_response_full"],
                    "schema_errors": errors,
                    **self._llm_message_metadata(result),
                },
            )
        )
        return (result.text if valid else ""), metadata

    def _run_rule_guided_custom_rule_scan_batches(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        runtime_settings,
        resolution,
        rule_screening: dict[str, object],
        normalized_batch_items: list[dict[str, object]],
        repository_context: dict[str, object],
        max_rules_per_batch: int,
        file_path: str,
        line_start: int,
        timeout_seconds: float,
    ) -> tuple[list[str], dict[str, object]]:
        rule_batches = self._split_custom_rule_scan_batches(
            rule_screening or {},
            max_rules_per_batch=max_rules_per_batch,
        )
        if not rule_batches:
            return [], {"attempted": False, "reason": "no_custom_rules"}

        targets = self._build_empty_candidate_retry_targets(
            normalized_batch_items=normalized_batch_items,
            fallback_file_path=file_path,
            fallback_line_start=line_start,
        )
        compact_context = self._clip_diagnostic_text(
            self._build_repository_context_summary(repository_context or {}, []),
            10000,
        )
        valid_texts: list[str] = []
        batch_metadata: list[dict[str, object]] = []
        for batch_index, rules in enumerate(rule_batches, start=1):
            required_rule_ids = [
                str(item.get("rule_id") or item.get("id") or "").strip()
                for item in rules
                if str(item.get("rule_id") or item.get("id") or "").strip()
            ]
            if not required_rule_ids:
                continue
            custom_prompt = "\n".join(
                [
                    "[CUSTOM_BOUND_RULE_REVIEW_ONLY]",
                    "本阶段只做专家绑定/产品/仓库自定义规范校验，和通用规范扫描相互补充，最终结果取并集。",
                    "必须严格逐条检查 CUSTOM_RULE_BATCH 中的规则，并全量扫描 TARGET_HUNKS 中的所有目标 hunk。",
                    "不要因为主审或规则预筛没有发现问题就跳过本阶段；本阶段以 CUSTOM_RULE_BATCH 为准重新校验。",
                    "如果某条规则不适用，输出 not_applicable；缺上下文输出 insufficient_context；存在当前代码证据时必须保留 candidate_findings 并写 context_requests。",
                    "candidate_findings 的 rule_id 必须来自 CUSTOM_RULE_BATCH；不得输出通用规则结论或编造规则 ID。",
                    "每条 candidate_finding 只能描述一个具体问题、一个主文件和一个主代码位置；title、evidence、reason、suggested_code 必须互相指向同一个自定义规则违反点。",
                    "candidate_finding 的 target_id、file_path、line 必须来自 TARGET_HUNKS；禁止照抄 OUTPUT_JSON 的占位说明，禁止使用不在 TARGET_HUNKS 中的文件。",
                    "输出根结构必须包含 rule_check_results、candidate_findings、context_requests、self_check。",
                    f"BATCH_INDEX: {batch_index}/{len(rule_batches)}",
                    f"REQUIRED_RULE_IDS: {json.dumps(required_rule_ids, ensure_ascii=False)}",
                    "",
                    "[CUSTOM_RULE_BATCH]",
                    json.dumps(rules, ensure_ascii=False, indent=2),
                    "",
                    "[TARGET_HUNKS]",
                    json.dumps(targets, ensure_ascii=False, indent=2),
                    "",
                    "[COMPACT_CONTEXT]",
                    compact_context,
                    "",
                    "[OUTPUT_JSON]",
                    json.dumps(
                        {
                            "rule_check_results": [
                                {
                                    "rule_id": "string",
                                    "status": "violated|passed|not_applicable|insufficient_context",
                                    "evidence": ["string"],
                                    "missing_context": ["string"],
                                    "reason": "string",
                                }
                            ],
                            "candidate_findings": [
                                {
                                    "rule_id": required_rule_ids[0],
                                    "title": "string",
                                    "target_id": "必须从 TARGET_HUNKS[].target_id 原样选择",
                                    "file_path": "必须从 TARGET_HUNKS[].file_path 原样选择",
                                    "line": "必须取对应 hunk changed_lines 中的当前代码行号",
                                    "evidence": "string",
                                    "confidence": "high|medium|low",
                                }
                            ],
                            "context_requests": [],
                            "self_check": {
                                "checked_all_rules": True,
                                "used_context_files": [],
                                "unverified_assumptions": [],
                            },
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "只输出一个 JSON 对象，不要输出 Markdown，不要添加额外解释。",
                ]
            )
            system_prompt = (
                "你是专家绑定规范批量校验器。只按本批 CUSTOM_RULE_BATCH 逐条审查目标 hunk；"
                "不要混入通用规范结论，不要编造规则 ID。只输出 JSON。"
            )
            prompt_contract = self._build_prompt_contract_metadata(
                stage="expert_custom_rule_batch_scan",
                system_prompt=system_prompt,
                user_prompt=custom_prompt,
                required_sections=["[CUSTOM_BOUND_RULE_REVIEW_ONLY]", "[CUSTOM_RULE_BATCH]", "[TARGET_HUNKS]", "[COMPACT_CONTEXT]", "[OUTPUT_JSON]"],
                forbidden_sections=["[GENERAL_EXPERT_PROFILE_REVIEW_ONLY]"],
                required_rule_ids=required_rule_ids,
                scope="custom bound RuleCard batch scan only",
            )
            try:
                result = self.llm_chat_service.complete_text(
                    system_prompt=system_prompt,
                    user_prompt=custom_prompt,
                    resolution=resolution,
                    runtime_settings=runtime_settings,
                    fallback_text=(
                        '{"rule_check_results":[],"candidate_findings":[],"context_requests":[]'
                        ',"self_check":{"checked_all_rules":false,"used_context_files":[],"unverified_assumptions":["custom rule batch fallback"]}}'
                    ),
                    allow_fallback=False,
                    timeout_seconds=max(20.0, min(float(timeout_seconds or 60), 75.0)),
                    max_attempts=1,
                    log_context={
                        "review_id": review.review_id,
                        "issue_id": "review_orchestration",
                        "expert_id": expert.expert_id,
                        "phase": "expert_custom_rule_batch_scan",
                        "file_path": file_path,
                        "line_start": line_start,
                        "batch_index": batch_index,
                        "prompt_contract": prompt_contract,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - one custom batch must not abort the expert review.
                batch_metadata.append(
                    {
                        "batch_index": batch_index,
                        "success": False,
                        "error": str(exc),
                        "required_rule_ids": required_rule_ids,
                    }
                )
                continue

            valid, errors = self._validate_rule_guided_llm_response_contract(
                result.text,
                required_rule_ids=required_rule_ids,
                allow_general_candidate_rule_id=False,
            )
            payload = self._parse_json_payload(result.text)
            candidate_count = (
                len([item for item in list(payload.get("candidate_findings") or []) if isinstance(item, dict)])
                if isinstance(payload, dict)
                else 0
            )
            metadata = {
                "batch_index": batch_index,
                "batch_count": len(rule_batches),
                "success": valid,
                "schema_errors": errors,
                "required_rule_ids": required_rule_ids,
                "candidate_count": candidate_count,
                "llm_call_id": result.call_id,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
                "prompt_snapshot_full": self._clip_diagnostic_text(custom_prompt, 120000),
                "raw_response_excerpt": self._clip_diagnostic_text(str(result.text or ""), 1600),
                "raw_response_full": self._clip_diagnostic_text(str(result.text or ""), 120000),
                "prompt_contract": prompt_contract,
            }
            batch_metadata.append(metadata)
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_custom_rule_batch_scan",
                    content=(
                        f"{expert.name_zh} 已完成第 {batch_index}/{len(rule_batches)} 批绑定规范校验，补充 {candidate_count} 条候选。"
                        if valid
                        else f"{expert.name_zh} 第 {batch_index}/{len(rule_batches)} 批绑定规范校验输出未满足结构合同。"
                    ),
                    metadata={
                        "phase": "expert_review",
                        "file_path": file_path,
                        "line_start": line_start,
                        "custom_rule_batch_scan": metadata,
                        "prompt_snapshot_full": metadata["prompt_snapshot_full"],
                        "model_raw_response_excerpt": metadata["raw_response_excerpt"],
                        "model_raw_response_full": metadata["raw_response_full"],
                        "schema_errors": errors,
                        **self._llm_message_metadata(result),
                    },
                )
            )
            if valid:
                valid_texts.append(result.text)

        return valid_texts, {
            "attempted": True,
            "batch_count": len(rule_batches),
            "success_count": len(valid_texts),
            "candidate_count": sum(int(item.get("candidate_count") or 0) for item in batch_metadata),
            "batches": batch_metadata,
        }

    def _split_custom_rule_scan_batches(
        self,
        rule_screening: dict[str, object],
        *,
        max_rules_per_batch: int,
    ) -> list[list[dict[str, object]]]:
        seen: set[str] = set()
        rules: list[dict[str, object]] = []
        source_rules: list[dict[str, object]] = []
        primary_rule_keys = (
            "matched_rules_for_llm",
            "must_review_rules",
            "possible_hit_rules",
        )
        for key in primary_rule_keys:
            for item in list((rule_screening or {}).get(key) or []):
                if isinstance(item, dict):
                    source_rules.append(item)
        if not source_rules:
            for item in list((rule_screening or {}).get("all_enabled_rules_for_llm") or []):
                if isinstance(item, dict):
                    source_rules.append(item)
        for item in source_rules:
            rule_id = str(item.get("rule_id") or item.get("id") or "").strip()
            if not rule_id or rule_id == "GENERAL-EXPERT-CHECKS" or rule_id in seen:
                continue
            seen.add(rule_id)
            rules.append(
                {
                    "rule_id": rule_id,
                    "title": str(item.get("title") or "").strip(),
                    "severity": str(item.get("priority") or item.get("severity") or "P2").strip(),
                    "must_check": self._extract_rule_guided_rule_list(item, "must_check_items", "must_check", fallback=[]),
                    "required_context": self._extract_rule_guided_rule_list(
                        item,
                        "required_context",
                        fallback=["changed_file_full_content", "repository_context"],
                    ),
                    "evidence_required": self._extract_rule_guided_rule_list(
                        item,
                        "evidence_required",
                        fallback=["明确代码行", "违反规则的原因"],
                    ),
                    "false_positive_guards": self._extract_rule_guided_rule_list(item, "false_positive_guards", fallback=[]),
                    "normalized_issue_type": str(item.get("normalized_issue_type") or "").strip(),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
            if len(rules) >= 48:
                break
        batch_size = max(1, min(max(1, int(max_rules_per_batch or 1)), 12))
        return [rules[index : index + batch_size] for index in range(0, len(rules), batch_size)]

    def _validate_rule_guided_llm_response_contract(
        self,
        text: str,
        *,
        required_rule_ids: list[str] | None = None,
        allow_general_candidate_rule_id: bool = True,
    ) -> tuple[bool, list[str]]:
        payload = self._parse_json_payload(text)
        errors: list[str] = []
        if not isinstance(payload, dict):
            return False, ["response_not_json_object"]
        rule_results = payload.get("rule_check_results")
        candidates = payload.get("candidate_findings")
        context_requests = payload.get("context_requests")
        self_check = payload.get("self_check")
        if not isinstance(rule_results, list):
            errors.append("rule_check_results_missing_or_not_list")
        if not isinstance(candidates, list):
            errors.append("candidate_findings_missing_or_not_list")
        if not isinstance(context_requests, list):
            errors.append("context_requests_missing_or_not_list")
        if not isinstance(self_check, dict):
            errors.append("self_check_missing_or_not_object")
        elif list(required_rule_ids or []):
            checked_all_rules = self_check.get("checked_all_rules")
            if checked_all_rules is not True and str(checked_all_rules).strip().lower() not in {
                "true",
                "yes",
                "y",
                "1",
                "是",
                "已检查",
            }:
                errors.append("self_check_checked_all_rules_not_true")
        if "findings" in payload:
            errors.append("legacy_findings_key_not_allowed")
        checked_rule_ids: set[str] = set()
        for index, item in enumerate(rule_results if isinstance(rule_results, list) else [], start=1):
            if not isinstance(item, dict):
                errors.append(f"rule_check_results_{index}_not_object")
                continue
            rule_id = str(item.get("rule_id") or "").strip()
            if not rule_id:
                errors.append(f"rule_check_results_{index}_rule_id_missing")
            else:
                checked_rule_ids.add(rule_id)
            status = self._normalize_rule_check_status(item.get("status"))
            if status not in {"violated", "passed", "not_applicable", "insufficient_context"}:
                errors.append(f"rule_check_results_{index}_status_invalid")
        for rule_id in list(required_rule_ids or []):
            if rule_id and rule_id not in checked_rule_ids:
                errors.append(f"rule_coverage_missing:{rule_id}")
        allowed_candidate_rule_ids = {
            str(rule_id).strip()
            for rule_id in list(required_rule_ids or [])
            if str(rule_id).strip()
        }
        for index, item in enumerate(candidates if isinstance(candidates, list) else [], start=1):
            if not isinstance(item, dict):
                errors.append(f"candidate_findings_{index}_not_object")
                continue
            for key in ("rule_id", "title", "file_path", "evidence"):
                if not str(item.get(key) or "").strip():
                    errors.append(f"candidate_findings_{index}_{key}_missing")
            candidate_rule_id = str(item.get("rule_id") or "").strip()
            if (
                allowed_candidate_rule_ids
                and candidate_rule_id
                and candidate_rule_id not in allowed_candidate_rule_ids
                and not (allow_general_candidate_rule_id and candidate_rule_id == "GENERAL-EXPERT-CHECKS")
            ):
                errors.append(f"candidate_findings_{index}_rule_id_not_in_required_rules:{candidate_rule_id}")
            if not (item.get("line") or item.get("line_start")):
                errors.append(f"candidate_findings_{index}_line_missing")
        return not errors, errors

    def _normalize_rule_check_status(self, value: object) -> str:
        status = str(value or "").strip().lower()
        aliases = {
            "fail": "violated",
            "failed": "violated",
            "failure": "violated",
            "violation": "violated",
            "违规": "violated",
            "违反": "violated",
            "不通过": "violated",
            "confirmed": "violated",
            "confirmed_issue": "violated",
            "pass": "passed",
            "ok": "passed",
            "通过": "passed",
            "not applicable": "not_applicable",
            "not-applicable": "not_applicable",
            "n/a": "not_applicable",
            "na": "not_applicable",
            "不适用": "not_applicable",
            "needs_context": "insufficient_context",
            "need_context": "insufficient_context",
            "missing_context": "insufficient_context",
            "insufficient context": "insufficient_context",
            "context_missing": "insufficient_context",
            "上下文不足": "insufficient_context",
            "证据不足": "insufficient_context",
        }
        return aliases.get(status, status)

    def _extract_rule_guided_context_requests(self, text: str) -> list[dict[str, object]]:
        payload = self._parse_json_payload(text)
        if not isinstance(payload, dict):
            return []
        requests: list[dict[str, object]] = []
        for item in list(payload.get("context_requests") or []):
            if not isinstance(item, dict):
                continue
            missing_context = str(item.get("missing_context") or "").strip()
            why_needed = str(item.get("why_needed") or "").strip()
            rule_id = str(item.get("rule_id") or "").strip()
            if not (missing_context or why_needed):
                continue
            requests.append(
                {
                    "rule_id": rule_id,
                    "missing_context": missing_context,
                    "why_needed": why_needed,
                }
            )
            if len(requests) >= 8:
                break
        return requests

    def _maybe_run_context_request_followup(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        runtime_settings,
        resolution,
        original_user_prompt: str,
        current_response: str,
        required_rule_ids: list[str],
        file_path: str,
        line_start: int,
        timeout_seconds: float,
    ) -> tuple[str, dict[str, object]]:
        context_requests = self._extract_rule_guided_context_requests(current_response)
        if not context_requests:
            return "", {}
        supplement = self._build_context_request_supplement(
            review.subject,
            runtime_settings,
            context_requests,
            file_path=file_path,
            line_start=line_start,
        )
        if not supplement.get("has_context"):
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_context_request_unresolved",
                    content=f"{expert.name_zh} 请求了补充上下文，但本地仓检索未命中可用片段。",
                    metadata={
                        "phase": "expert_review",
                        "file_path": file_path,
                        "line_start": line_start,
                        "context_requests": context_requests,
                        "context_supplement": supplement,
                    },
                )
            )
            return "", {"attempted": True, "resolved": False, "context_requests": context_requests}
        followup_prompt = "\n".join(
            [
                "你是代码审查专家。下面是你刚才请求的补充上下文，请基于原任务、原响应和补充上下文重新输出完整规则驱动 JSON。",
                "必须逐条覆盖 REQUIRED_RULE_IDS；禁止输出 legacy findings 根结构；只输出 JSON。",
                f"REQUIRED_RULE_IDS: {json.dumps(required_rule_ids, ensure_ascii=False)}",
                "",
                "[CONTEXT_REQUEST_SUPPLEMENT]",
                json.dumps(supplement, ensure_ascii=False, indent=2),
                "",
                "[PREVIOUS_RESPONSE]",
                self._clip_diagnostic_text(str(current_response or ""), 12000),
                "",
                "[ORIGINAL_PROMPT_SUMMARY]",
                self._clip_diagnostic_text(str(original_user_prompt or ""), 12000),
            ]
        )
        try:
            followup_result = self.llm_chat_service.complete_text(
                system_prompt="根据补充上下文重新输出规则驱动 JSON。禁止 Markdown，禁止解释。",
                user_prompt=followup_prompt,
                resolution=resolution,
                runtime_settings=runtime_settings,
                fallback_text=current_response,
                allow_fallback=False,
                timeout_seconds=max(20.0, min(float(timeout_seconds or 60), 75.0)),
                max_attempts=1,
                log_context={
                    "review_id": review.review_id,
                    "issue_id": "review_orchestration",
                    "expert_id": expert.expert_id,
                    "phase": "expert_context_request_followup",
                    "file_path": file_path,
                    "line_start": line_start,
                },
            )
        except Exception as exc:  # noqa: BLE001 - follow-up failure must not abort the review.
            return "", {
                "attempted": True,
                "resolved": True,
                "success": False,
                "error": str(exc),
                "context_requests": context_requests,
            }
        valid, errors = self._validate_rule_guided_llm_response_contract(
            followup_result.text,
            required_rule_ids=required_rule_ids,
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_context_request_followup",
                content=(
                    f"{expert.name_zh} 已基于 context_requests 补充上下文进行二次规则驱动审查。"
                    if valid
                    else f"{expert.name_zh} 的 context_requests 二次审查输出仍未满足结构合同。"
                ),
                metadata={
                    "phase": "expert_review",
                    "file_path": file_path,
                    "line_start": line_start,
                    "context_requests": context_requests,
                    "context_supplement": supplement,
                    "schema_valid": valid,
                    "schema_errors": errors,
                    "prompt_snapshot_full": self._clip_diagnostic_text(followup_prompt, 120000),
                    "model_raw_response_excerpt": self._clip_diagnostic_text(str(followup_result.text or ""), 1600),
                    "model_raw_response_full": self._clip_diagnostic_text(str(followup_result.text or ""), 120000),
                    **self._llm_message_metadata(followup_result),
                },
            )
        )
        metadata = {
            "attempted": True,
            "resolved": True,
            "success": valid,
            "schema_errors": errors,
            "context_requests": context_requests,
            "llm_call_id": followup_result.call_id,
            "prompt_tokens": followup_result.prompt_tokens,
            "completion_tokens": followup_result.completion_tokens,
            "total_tokens": followup_result.total_tokens,
            "prompt_snapshot_full": self._clip_diagnostic_text(followup_prompt, 120000),
            "model_raw_response_full": self._clip_diagnostic_text(str(followup_result.text or ""), 120000),
        }
        return (followup_result.text if valid else ""), metadata

    def _build_context_request_supplement(
        self,
        subject: ReviewSubject,
        runtime_settings,
        context_requests: list[dict[str, object]],
        *,
        file_path: str,
        line_start: int,
    ) -> dict[str, object]:
        service = self.repository_resolver.build_context_service(runtime_settings, subject)
        queries = self._context_request_queries(context_requests)
        primary_context: dict[str, object] = {}
        search_result: dict[str, object] = {"queries": queries, "matches": []}
        if service.is_ready():
            primary_context = service.load_file_context(file_path, line_start, radius=28)
            search_result = service.search_many(queries, limit_per_query=4, total_limit=16)
        matches = [dict(item) for item in list(search_result.get("matches") or []) if isinstance(item, dict)]
        related_contexts: list[dict[str, object]] = []
        seen_paths: set[str] = set()
        for match in matches:
            path = str(match.get("path") or "").strip()
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            related_contexts.append(
                service.load_file_context(path, int(match.get("line_number") or 1), radius=18)
                if service.is_ready()
                else {"path": path, "snippet": "", "line_start": int(match.get("line_number") or 1)}
            )
            if len(related_contexts) >= 6:
                break
        has_context = bool(str(primary_context.get("snippet") or "").strip() or matches or related_contexts)
        return {
            "has_context": has_context,
            "repository_ready": service.is_ready(),
            "repository_path": str(getattr(service, "local_path", "") or ""),
            "context_requests": context_requests,
            "queries": queries,
            "primary_context": primary_context,
            "matches": matches[:12],
            "related_contexts": related_contexts,
        }

    def _context_request_queries(self, context_requests: list[dict[str, object]]) -> list[str]:
        queries: list[str] = []
        stop_words = {
            "changed_file_full_content",
            "repository_context",
            "current_file",
            "target_hunk",
            "source_context",
        }
        for request in context_requests:
            for value in (
                request.get("missing_context"),
                request.get("why_needed"),
                request.get("rule_id"),
            ):
                raw = str(value or "").strip()
                if not raw:
                    continue
                for token in re.split(r"[^A-Za-z0-9_.$#]+", raw):
                    normalized = token.strip("._-$#")
                    if len(normalized) < 3 or normalized in stop_words:
                        continue
                    if normalized not in queries:
                        queries.append(normalized)
                    if len(queries) >= 10:
                        return queries
        return queries[:10]

    def _repair_rule_guided_llm_response_schema(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        runtime_settings,
        resolution,
        original_user_prompt: str,
        original_response: str,
        rule_screening: dict[str, object],
        required_rule_ids: list[str],
        file_path: str,
        line_start: int,
        max_findings: int,
        timeout_seconds: float,
    ) -> tuple[str, dict[str, object]]:
        rule_ids = list(required_rule_ids or []) or self._rule_guided_required_rule_ids(
            rule_screening,
            max_rules_per_prompt=20,
        )
        local_repair_text, local_repair_metadata = self._repair_legacy_findings_response_locally(
            original_response=original_response,
            required_rule_ids=rule_ids,
            file_path=file_path,
            line_start=line_start,
            max_findings=max_findings,
        )
        if local_repair_text:
            return local_repair_text, local_repair_metadata
        repair_prompt = "\n".join(
            [
                "你是代码审查结果结构化修复器。只允许把原始专家输出转换为规则驱动 JSON，不要新增原始输出中没有的代码问题。",
                "如果原始输出已有问题但没有 rule_id，请优先从 RULE_IDS 中选择最贴近的规则；仍无法判断时使用 GENERAL-EXPERT-CHECKS。",
                "如果原始输出表示无问题，candidate_findings 输出空数组，但仍要输出 rule_check_results。",
                "",
                f"目标文件: {file_path}",
                f"目标行号: {line_start}",
                f"最大候选数: {max(1, int(max_findings or 1))}",
                f"RULE_IDS: {json.dumps(rule_ids or ['GENERAL-EXPERT-CHECKS'], ensure_ascii=False)}",
                "",
                "必须只输出一个 JSON 对象，结构如下：",
                json.dumps(
                    {
                        "rule_check_results": [
                            {
                                "rule_id": "string",
                                "status": "violated|passed|not_applicable|insufficient_context",
                                "evidence": ["string"],
                                "missing_context": ["string"],
                                "reason": "string",
                            }
                        ],
                        "candidate_findings": [
                            {
                                "rule_id": "string",
                                "title": "string",
                                "file_path": file_path,
                                "line": line_start,
                                "evidence": "string",
                                "confidence": "high|medium|low",
                            }
                        ],
                        "context_requests": [],
                        "self_check": {
                            "checked_all_rules": True,
                            "used_context_files": [],
                            "unverified_assumptions": [],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
                "[原始专家输出]",
                self._clip_diagnostic_text(str(original_response or ""), 12000),
                "",
                "[原始任务提示摘要]",
                self._clip_diagnostic_text(str(original_user_prompt or ""), 8000),
            ]
        )
        try:
            repair_result = self.llm_chat_service.complete_text(
                system_prompt="只做 JSON 结构化修复。禁止 Markdown，禁止解释，禁止新增代码问题。",
                user_prompt=repair_prompt,
                resolution=resolution,
                runtime_settings=runtime_settings,
                fallback_text='{"rule_check_results":[],"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":false,"used_context_files":[],"unverified_assumptions":["schema repair fallback"]}}',
                allow_fallback=False,
                timeout_seconds=max(15.0, min(float(timeout_seconds or 60), 60.0)),
                max_attempts=1,
                log_context={
                    "review_id": review.review_id,
                    "issue_id": "review_orchestration",
                    "expert_id": expert.expert_id,
                    "phase": "expert_schema_repair",
                    "file_path": file_path,
                    "line_start": line_start,
                },
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics must survive repair failures.
            return "", {"repair_attempted": True, "repair_success": False, "repair_error": str(exc)}
        repair_valid, repair_errors = self._validate_rule_guided_llm_response_contract(repair_result.text)
        return repair_result.text, {
            "repair_attempted": True,
            "repair_success": repair_valid,
            "repair_errors": repair_errors,
            "repair_llm_call_id": repair_result.call_id,
            "repair_prompt_tokens": repair_result.prompt_tokens,
            "repair_completion_tokens": repair_result.completion_tokens,
            "repair_total_tokens": repair_result.total_tokens,
            "repair_error": repair_result.error,
            "repair_prompt_snapshot_full": self._clip_diagnostic_text(repair_prompt, 120000),
            "repair_raw_response_full": self._clip_diagnostic_text(str(repair_result.text or ""), 120000),
        }

    def _repair_legacy_findings_response_locally(
        self,
        *,
        original_response: str,
        required_rule_ids: list[str],
        file_path: str,
        line_start: int,
        max_findings: int,
    ) -> tuple[str, dict[str, object]]:
        """Convert legacy findings JSON into the rule-guided contract without accepting legacy directly."""

        payload = self._parse_json_payload(original_response)
        if not isinstance(payload, dict):
            return "", {}
        raw_findings = payload.get("findings")
        if not isinstance(raw_findings, list) and any(
            str(payload.get(key) or "").strip()
            for key in ("title", "claim", "summary", "evidence", "matched_rules", "violated_guidelines")
        ):
            raw_findings = [payload]
        if not isinstance(raw_findings, list):
            return "", {}
        safe_required_rule_ids = [
            str(rule_id).strip()
            for rule_id in list(required_rule_ids or [])
            if str(rule_id).strip()
        ] or ["GENERAL-EXPERT-CHECKS"]
        max_count = max(1, int(max_findings or 1))
        candidate_findings: list[dict[str, object]] = []
        checked_rule_ids: set[str] = set()
        for raw in raw_findings[:max_count]:
            if not isinstance(raw, dict):
                continue
            raw_matched_rules = self._normalize_text_list(raw.get("matched_rules"), [])
            raw_violated_guidelines = self._normalize_text_list(raw.get("violated_guidelines"), [])
            matched_rules = [
                str(item).strip()
                for item in [*raw_matched_rules, *raw_violated_guidelines]
                if str(item).strip()
            ]
            rule_id = ""
            for item in matched_rules:
                if item in safe_required_rule_ids:
                    rule_id = item
                    break
                extracted_ids = self._extract_additive_rule_ids(item)
                matched_known_id = next(
                    (
                        known_id
                        for known_id in safe_required_rule_ids
                        if known_id in extracted_ids or known_id in item
                    ),
                    "",
                )
                if matched_known_id:
                    rule_id = matched_known_id
                    break
            if not rule_id:
                rule_id = "GENERAL-EXPERT-CHECKS"
            title = str(raw.get("title") or raw.get("claim") or "代码检视候选问题").strip()
            evidence_items = self._normalize_text_list(raw.get("evidence"), [])
            evidence = "；".join(evidence_items[:4]) or str(raw.get("claim") or title).strip()
            line = raw.get("line_start") or raw.get("line") or line_start
            candidate_findings.append(
                {
                    "rule_id": rule_id,
                    "title": title,
                    "claim": str(raw.get("claim") or raw.get("summary") or title).strip(),
                    "file_path": str(raw.get("file_path") or file_path).strip() or file_path,
                    "line": int(line or line_start or 1),
                    "line_start": int(line or line_start or 1),
                    "line_end": int(raw.get("line_end") or line or line_start or 1),
                    "evidence": evidence,
                    "confidence": self._string_confidence_label(raw.get("confidence")),
                    "matched_rules": raw_matched_rules,
                    "violated_guidelines": raw_violated_guidelines,
                    "finding_type": str(raw.get("finding_type") or "").strip(),
                    "normalized_issue_type": str(raw.get("normalized_issue_type") or "").strip(),
                    "severity": str(raw.get("severity") or "").strip(),
                    "fix_strategy": str(raw.get("fix_strategy") or "").strip(),
                    "suggested_fix": str(raw.get("suggested_fix") or "").strip(),
                    "suggested_code": str(raw.get("suggested_code") or "").strip(),
                    "change_steps": [
                        str(item).strip()
                        for item in list(raw.get("change_steps") or [])
                        if str(item).strip()
                    ],
                    "cross_file_evidence": [
                        str(item).strip()
                        for item in list(raw.get("cross_file_evidence") or [])
                        if str(item).strip()
                    ],
                    "assumptions": [
                        str(item).strip()
                        for item in list(raw.get("assumptions") or [])
                        if str(item).strip()
                    ],
                    "context_files": [
                        str(item).strip()
                        for item in list(raw.get("context_files") or [])
                        if str(item).strip()
                    ],
                    "observation_ids": [
                        str(item).strip()
                        for item in list(raw.get("observation_ids") or [])
                        if str(item).strip()
                    ],
                }
            )
            checked_rule_ids.add(rule_id)
        if not candidate_findings:
            return "", {}
        rule_check_results: list[dict[str, object]] = []
        rule_result_ids = list(safe_required_rule_ids)
        for candidate in candidate_findings:
            candidate_rule_id = str(candidate.get("rule_id") or "").strip()
            if candidate_rule_id and candidate_rule_id not in rule_result_ids:
                rule_result_ids.append(candidate_rule_id)
        for rule_id in rule_result_ids:
            violated = rule_id in checked_rule_ids
            rule_check_results.append(
                {
                    "rule_id": rule_id,
                    "status": "violated" if violated else "passed",
                    "evidence": (
                        ["legacy findings converted; candidate_findings carry per-candidate evidence"]
                        if violated
                        else []
                    ),
                    "missing_context": [],
                    "reason": (
                        "由 legacy findings 结构化修复得到候选，后续仍需按规则驱动校验。"
                        if violated
                        else "legacy 输出中未发现该规则对应候选。"
                    ),
                }
            )
        repaired_payload = {
            "rule_check_results": rule_check_results,
            "candidate_findings": candidate_findings,
            "context_requests": [],
            "self_check": {
                "checked_all_rules": True,
                "used_context_files": [],
                "unverified_assumptions": ["legacy findings converted by schema repair"],
            },
        }
        text = json.dumps(repaired_payload, ensure_ascii=False)
        valid, errors = self._validate_rule_guided_llm_response_contract(
            text,
            required_rule_ids=safe_required_rule_ids,
        )
        return (
            text if valid else "",
            {
                "repair_attempted": True,
                "repair_success": valid,
                "repair_mode": "local_legacy_findings_conversion",
                "repair_errors": errors,
                "converted_candidate_count": len(candidate_findings),
                "required_rule_ids": safe_required_rule_ids,
            },
        )

    def _string_confidence_label(self, value: object) -> str:
        if isinstance(value, (int, float)):
            score = float(value)
            if score >= 0.85:
                return "high"
            if score >= 0.65:
                return "medium"
            return "low"
        label = str(value or "").strip().lower()
        if label in {"high", "medium", "low"}:
            return label
        if label in {"高", "较高"}:
            return "high"
        if label in {"中", "中等"}:
            return "medium"
        if label in {"低", "较低"}:
            return "low"
        return "medium"

    def _clip_diagnostic_text(self, text: str, max_chars: int) -> str:
        safe_max = max(200, int(max_chars or 200))
        value = str(text or "")
        if len(value) <= safe_max:
            return value
        return value[:safe_max].rstrip() + "\n...[truncated]"

    def _has_live_llm_call(self, review_id: str) -> bool:
        for message in self.message_repo.list(review_id):
            metadata = dict(message.metadata or {})
            if (
                str(metadata.get("mode") or "").strip().lower() == "live"
                and str(metadata.get("llm_call_id") or "").strip()
            ):
                return True
        return False

    def _score_finding(self, subject: ReviewSubject, expert_id: str) -> tuple[str, float]:
        file_blob = " ".join(subject.changed_files).lower()
        if expert_id == "security_compliance" and any(
            token in file_blob for token in ["auth", "security", "permission", "token"]
        ):
            return "blocker", 0.91
        if expert_id == "database_analysis" and any(
            token in file_blob for token in ["migration", "sql", "schema", "db", "repository"]
        ):
            return "high", 0.88
        if expert_id == "redis_analysis" and any(token in file_blob for token in ["redis", "cache"]):
            return "high", 0.87
        if expert_id == "mq_analysis" and any(
            token in file_blob for token in ["mq", "kafka", "rocketmq", "rabbit", "queue", "consumer", "producer"]
        ):
            return "high", 0.87
        if expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
            return "medium", 0.83
        if expert_id == "performance_reliability" and any(
            token in file_blob for token in ["migration", "sql", "repository", "db"]
        ):
            return "high", 0.86
        if expert_id == "test_verification":
            return "medium", 0.75
        if expert_id == "maintainability_code_health":
            return "low", 0.7
        return "medium", 0.8

    def _pick_file_path(self, subject: ReviewSubject, expert_id: str) -> str:
        if not subject.changed_files:
            return "src/example.ts"
        file_blob = " ".join(subject.changed_files).lower()
        if expert_id == "security_compliance":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["auth", "security", "permission", "token"]):
                    return file_path
        if expert_id == "performance_reliability":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["migration", "sql", "repository", "db"]):
                    return file_path
        if expert_id == "database_analysis":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["migration", "sql", "schema", "db", "repository", "dao"]):
                    return file_path
        if expert_id == "redis_analysis":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["redis", "cache"]):
                    return file_path
        if expert_id == "mq_analysis":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["mq", "kafka", "rocketmq", "rabbit", "queue", "consumer", "producer"]):
                    return file_path
        if expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["domain", "aggregate", "entity", "repository", "service", "application"]):
                    return file_path
        if expert_id == "test_verification":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["test", "spec", "playwright", "jest", "vitest"]):
                    return file_path
        if "frontend" in file_blob:
            for file_path in subject.changed_files:
                if "frontend" in file_path.lower():
                    return file_path
        return subject.changed_files[0]

    def _pick_line_start(self, subject: ReviewSubject, expert_id: str) -> int:
        file_blob = " ".join(subject.changed_files).lower()
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
        elif expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
            preferred_line = 40
        elif expert_id == "test_verification":
            preferred_line = 73

        file_path = self._pick_file_path(subject, expert_id)
        return self.diff_excerpt_service.find_nearest_line(
            subject.unified_diff,
            file_path,
            preferred_line,
        ) or preferred_line

    def _build_evidence(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        tool_evidence: list[dict[str, object]],
        parsed: dict[str, object],
    ) -> list[str]:
        evidence = [expert.focus_areas[0] if expert.focus_areas else expert.role]
        for item in parsed.get("evidence", []):
            text = str(item).strip()
            if text:
                evidence.append(text)
        lowered_file_path = file_path.lower()
        if expert.expert_id in {"database_analysis", "performance_reliability"} or any(
            token in lowered_file_path for token in ["migration", ".sql", "schema", "db", "repository"]
        ):
            evidence.append("database_migration")
        if expert.expert_id == "security_compliance" or any(
            token in lowered_file_path for token in ["auth", "security", "permission", "token", "secret"]
        ):
            evidence.append("security_surface")
        if expert.expert_id == "test_verification" or any(
            token in lowered_file_path for token in ["test", "spec", "jest", "vitest", "playwright"]
        ):
            evidence.append("test_surface")
        for tool_result in tool_evidence:
            tool_name = str(tool_result.get("tool_name") or "")
            summary = str(tool_result.get("summary") or "").strip()
            evidence.append(f"{tool_name}:{summary}" if summary else tool_name)
        deduped: list[str] = []
        for item in evidence:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    def _verify_rule_guided_candidate_before_persist(
        self,
        *,
        parsed: dict[str, object],
        finding_file_path: str,
        parsed_line_start: int,
        rule_screening: dict[str, object],
        repository_context: dict[str, object],
        input_completeness: dict[str, object],
    ) -> dict[str, object]:
        if not bool(parsed.get("rule_guided_candidate")):
            return {}
        rules_by_id = self._build_review_rule_cards_from_screening(rule_screening)
        rule_id = str(parsed.get("rule_id") or "").strip()
        if not rule_id:
            rule_id = next((str(item).strip() for item in list(parsed.get("matched_rules") or []) if str(item).strip()), "")
        if rule_id and rule_id != "GENERAL-EXPERT-CHECKS" and rule_id not in rules_by_id:
            known_rule_id = next(
                (
                    str(item).strip()
                    for item in list(parsed.get("matched_rules") or [])
                    if str(item).strip() in rules_by_id
                ),
                "",
            )
            if known_rule_id:
                rule_id = known_rule_id
            else:
                extracted_known_rule_id = ""
                for item in list(parsed.get("matched_rules") or []):
                    for candidate_rule_id in self._extract_additive_rule_ids(str(item or "")):
                        if candidate_rule_id in rules_by_id:
                            extracted_known_rule_id = candidate_rule_id
                            break
                    if extracted_known_rule_id:
                        break
                rule_id = extracted_known_rule_id or "GENERAL-EXPERT-CHECKS"
        if not rule_id:
            return {
                "status": "rejected",
                "reasons": ["missing_rule_id"],
                "missing_context": [],
                "matched_false_positive_guards": [],
            }
        evidence = " / ".join(str(item).strip() for item in list(parsed.get("evidence") or []) if str(item).strip())
        try:
            candidate = CandidateFinding(
                rule_id=rule_id,
                title=str(parsed.get("title") or "").strip(),
                file_path=finding_file_path,
                line=int(parsed_line_start or 1),
                evidence=evidence,
                confidence="high" if float(parsed.get("confidence") or 0.0) >= 0.8 else "medium",
            )
        except Exception as exc:  # pydantic validation should reject malformed candidates.
            return {
                "status": "rejected",
                "reasons": [f"candidate_invalid:{exc.__class__.__name__}"],
                "missing_context": [],
                "matched_false_positive_guards": [],
            }
        rule_results_by_id = {
            rule_id: ReviewRuleCheckResult(
                rule_id=rule_id,
                status=str(parsed.get("rule_check_status") or "violated"),
                evidence=list(parsed.get("evidence") or []),
                missing_context=list(parsed.get("missing_context") or []),
                reason=str(parsed.get("rule_based_reasoning") or "").strip(),
            )
        }
        verification = verify_candidate_finding(
            candidate,
            rules_by_id=rules_by_id,
            rule_results_by_id=rule_results_by_id,
            loaded_context=self._loaded_context_keys_for_verification(repository_context, input_completeness),
        )
        return {
            "status": verification.status,
            "reasons": list(verification.reasons),
            "missing_context": list(verification.missing_context),
            "matched_false_positive_guards": list(verification.matched_false_positive_guards),
            "rule_id": candidate.rule_id,
        }

    def _build_review_rule_cards_from_screening(self, rule_screening: dict[str, object]) -> dict[str, ReviewRuleCard]:
        cards: dict[str, ReviewRuleCard] = {}
        for item in list((rule_screening or {}).get("matched_rules_for_llm") or []):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or "").strip()
            if not rule_id:
                continue
            cards[rule_id] = ReviewRuleCard(
                rule_id=rule_id,
                title=str(item.get("title") or rule_id).strip(),
                scope=["review"],
                must_check=self._normalize_text_list(item.get("must_check_items") or item.get("must_check"), ["按规则检查当前变更"]),
                required_context=self._normalize_text_list(
                    item.get("required_context"),
                    ["changed_file_full_content", "repository_context"],
                ),
                evidence_required=self._normalize_text_list(
                    item.get("evidence_required"),
                    ["代码证据", "规则违反原因"],
                ),
                false_positive_guards=self._normalize_text_list(item.get("false_positive_guards"), []),
                severity_default=str(item.get("priority") or "medium").strip() or "medium",
                normalized_issue_type=str(item.get("normalized_issue_type") or "rule_guided_candidate").strip(),
            )
        return cards

    def _loaded_context_keys_for_verification(
        self,
        repository_context: dict[str, object],
        input_completeness: dict[str, object],
    ) -> set[str]:
        loaded: set[str] = set()
        if bool(input_completeness.get("target_file_diff_present")):
            loaded.add("changed_file_full_content")
        if repository_context:
            loaded.add("repository_context")
        if bool(input_completeness.get("source_context_present")):
            loaded.add("source_context")
            loaded.add("current_class_context")
        if int(input_completeness.get("related_context_count") or 0) > 0:
            loaded.add("related_context")
            loaded.add("caller_context")
            loaded.add("callee_context")
        for key in (
            "current_class_context",
            "caller_contexts",
            "callee_contexts",
            "domain_model_contexts",
            "persistence_contexts",
            "transaction_context",
            "related_contexts",
        ):
            value = repository_context.get(key)
            if value:
                loaded.add(key)
        if repository_context.get("domain_model_contexts") or repository_context.get("related_contexts"):
            loaded.add("aggregate_root_definition")
            loaded.add("aggregate_factory_method")
            loaded.add("domain_event_publication")
        return loaded

    def _match_sast_prescan_findings(
        self,
        *,
        parsed: dict[str, object],
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
    ) -> list[dict[str, object]]:
        sast_prescan = repository_context.get("sast_prescan")
        if not isinstance(sast_prescan, dict) or not sast_prescan.get("enabled"):
            return []
        findings = [dict(item) for item in list(sast_prescan.get("findings") or []) if isinstance(item, dict)]
        if not findings:
            return []
        target_path = self._normalize_path_for_match(file_path)
        text_blob = " ".join(
            [
                str(parsed.get("title") or ""),
                str(parsed.get("claim") or ""),
                str(parsed.get("summary") or ""),
                str(parsed.get("normalized_issue_type") or ""),
                *[str(item) for item in list(parsed.get("evidence") or [])],
                *[str(item) for item in list(parsed.get("matched_rules") or [])],
            ]
        ).lower()
        matches: list[dict[str, object]] = []
        for item in findings:
            item_path = self._normalize_path_for_match(str(item.get("file_path") or item.get("path") or ""))
            if item_path and target_path and item_path != target_path and not item_path.endswith("/" + target_path):
                continue
            item_line = self._safe_int(item.get("line_start") or item.get("line") or 0, 0)
            line_matches = item_line <= 0 or abs(item_line - int(line_start or 1)) <= 3
            rule_id = str(item.get("rule_id") or "").strip()
            message = str(item.get("message") or "").strip()
            text_matches = bool(rule_id and rule_id.lower() in text_blob) or self._has_sast_text_overlap(text_blob, message)
            if line_matches and (text_matches or not message):
                matches.append(
                    {
                        "tool": str(item.get("tool") or "sast").strip(),
                        "rule_id": rule_id,
                        "message": message,
                        "severity": str(item.get("severity") or "").strip(),
                        "cwe": str(item.get("cwe") or "").strip(),
                        "why_it_matters": str(item.get("why_it_matters") or "").strip(),
                        "file_path": str(item.get("file_path") or file_path).strip(),
                        "line_start": item_line or int(line_start or 1),
                    }
                )
        return matches[:4]

    def _has_sast_text_overlap(self, text_blob: str, message: str) -> bool:
        tokens = [
            token
            for token in re.split(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", str(message or "").lower())
            if len(token) >= 4 and token not in {"warning", "error", "medium", "high", "rule", "message"}
        ]
        return bool(tokens and any(token in text_blob for token in tokens[:8]))

    def _format_sast_prescan_evidence(self, item: dict[str, object]) -> str:
        tool = str(item.get("tool") or "sast").strip()
        rule_id = str(item.get("rule_id") or "rule").strip()
        line_start = self._safe_int(item.get("line_start"), 1)
        message = str(item.get("message") or "").strip()
        cwe = str(item.get("cwe") or "").strip()
        why = str(item.get("why_it_matters") or "").strip()
        suffix = f" ({cwe})" if cwe else ""
        detail = f"；{why}" if why else ""
        return f"SAST/linter 佐证: {tool}:{rule_id} L{line_start}{suffix} {message}{detail}".strip()

    def _normalize_path_for_match(self, value: str) -> str:
        return str(value or "").strip().replace("\\", "/").lstrip("./")

    def _dedupe_texts(self, values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                deduped.append(text)
        return deduped

    def _safe_int(self, value: object, default: int) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def _build_finding_summary(self, subject: ReviewSubject, expert_id: str) -> str:
        file_blob = ", ".join(subject.changed_files[:2]) or subject.source_ref
        if expert_id == "security_compliance":
            return f"鉴权或敏感路径变更涉及 {file_blob}，当前实现没有充分体现权限边界、失败路径或敏感数据保护。"
        if expert_id == "performance_reliability":
            return f"数据访问或迁移路径变更涉及 {file_blob}，当前实现对锁粒度、回滚策略或资源影响的处理不完整。"
        if expert_id == "database_analysis":
            return f"数据库相关变更涉及 {file_blob}，当前实现对 schema 演进、索引影响、事务边界或回滚路径说明不足。"
        if expert_id == "redis_analysis":
            return f"缓存路径变更涉及 {file_blob}，当前实现对 key 设计、过期策略、一致性或击穿保护说明不足。"
        if expert_id == "mq_analysis":
            return f"消息链路变更涉及 {file_blob}，当前实现对消息顺序、幂等、重试和死信处理交代不足。"
        if expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
            return f"领域建模相关改动涉及 {file_blob}，当前实现可能混淆领域规则、应用编排和基础设施职责，偏离 DDD 分层。"
        if expert_id == "test_verification":
            return f"当前改动涉及 {file_blob}，缺少与改动风险相匹配的回归测试或更强断言保护。"
        if expert_id == "architecture_design":
            return f"当前改动涉及 {file_blob}，命名、判空、异常、日志或常量写法存在高价值通用编码规范问题，容易增加误用和排查成本。"
        if expert_id == "maintainability_code_health":
            return f"当前改动涉及 {file_blob}，实现把规则和流程揉在一起，后续维护和排错成本偏高。"
        return f"当前改动涉及 {file_blob}，存在需要进一步修正的实现风险，当前写法缺少足够的边界说明与保护。"

    def _build_finding_title(self, expert: ExpertProfile) -> str:
        if expert.expert_id == "security_compliance":
            return "权限与敏感路径保护不足"
        if expert.expert_id == "performance_reliability":
            return "资源与回滚控制存在风险"
        if expert.expert_id == "database_analysis":
            return "数据库演进与事务边界存在风险"
        if expert.expert_id == "redis_analysis":
            return "缓存一致性与失效策略存在风险"
        if expert.expert_id == "mq_analysis":
            return "消息幂等与重试语义存在风险"
        if expert.expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
            return "领域边界与职责分层偏离 DDD 规范"
        if expert.expert_id == "test_verification":
            return "缺少与改动匹配的验证保护"
        if expert.expert_id == "architecture_design":
            return "通用编码规范与工程一致性存在风险"
        if expert.expert_id == "maintainability_code_health":
            return "实现耦合偏高，维护成本上升"
        return f"{expert.name_zh} 识别到待修复问题"

    def _build_remediation_suggestion(
        self,
        subject: ReviewSubject,
        expert_id: str,
        file_path: str,
    ) -> str:
        if expert_id == "security_compliance":
            return f"在 {file_path} 增加明确的权限校验、失败分支和敏感字段保护，并补充拒绝场景测试。"
        if expert_id == "performance_reliability":
            return f"在 {file_path} 拆出显式的回滚与超时控制，补充资源释放和慢路径保护。"
        if expert_id == "database_analysis":
            return f"在 {file_path} 明确事务边界、回滚策略和索引影响，并补充 schema 变更验证与回退脚本。"
        if expert_id == "redis_analysis":
            return f"在 {file_path} 明确 key 设计、TTL、一致性策略和缓存失效路径，并补充击穿/脏读保护。"
        if expert_id == "mq_analysis":
            return f"在 {file_path} 补充消息幂等键、重试上限、死信处理和消费顺序约束。"
        if expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
            return f"在 {file_path} 把领域规则、应用服务编排和基础设施访问重新分层，避免聚合职责外溢。"
        if expert_id == "test_verification":
            return f"围绕 {file_path} 的关键分支补充回归测试，并为异常路径增加断言。"
        if expert_id == "architecture_design":
            return f"在 {file_path} 收紧命名、常量、判空、异常和日志写法，先把高价值通用编码规范问题修平。"
        if expert_id == "maintainability_code_health":
            return f"把 {file_path} 中的条件分支和魔法值提取成独立函数或策略对象，降低后续维护成本。"
        return f"重构 {file_path} 的当前实现，补充边界保护与必要注释，并为关键路径增加测试。"

    def _build_remediation_strategy(
        self,
        subject: ReviewSubject,
        expert_id: str,
        file_path: str,
    ) -> str:
        if expert_id == "security_compliance":
            return f"先把 {file_path} 的权限边界前置，再把失败分支和敏感字段保护收紧到主流程入口。"
        if expert_id == "performance_reliability":
            return f"围绕 {file_path} 先收敛慢路径、回滚和资源释放，再考虑继续扩展功能。"
        if expert_id == "database_analysis":
            return f"对 {file_path} 采用兼容性优先的数据库变更策略，先保证线上安全，再收紧约束。"
        if expert_id == "redis_analysis":
            return f"围绕 {file_path} 先固定 key/TTL/失效顺序，再补缓存一致性和热点保护。"
        if expert_id == "mq_analysis":
            return f"在 {file_path} 先明确幂等、重试、死信策略，再落消费逻辑调整。"
        if expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
            return f"把 {file_path} 的领域规则、应用编排和基础设施访问重新拆层，先收回职责边界。"
        if expert_id == "test_verification":
            return f"先补测试锁住 {file_path} 当前风险，再根据断言结果决定是否继续改实现。"
        if expert_id == "architecture_design":
            return f"先修正 {file_path} 的命名、常量、判空、异常和日志表达，再统一局部写法，降低误用与排查成本。"
        if expert_id == "maintainability_code_health":
            return f"先把 {file_path} 的重复判断和复杂分支提炼掉，再补命名和注释，降低维护成本。"
        return f"围绕 {file_path} 先缩小修改面、拉直主流程，再用更清晰的代码结构替换当前实现。"

    def _build_remediation_steps(
        self,
        subject: ReviewSubject,
        expert_id: str,
        file_path: str,
    ) -> list[str]:
        common_steps = [
            f"先定位 {file_path} 中这次问题对应的主流程入口，只修改当前风险真正命中的代码路径。",
            "把条件判断、字段处理或依赖调用拆成更明确的步骤，避免一个分支同时承担多种职责。",
            "补一组与当前风险直接对应的回归测试，至少覆盖正常路径、失败路径和边界输入。",
        ]
        if expert_id == "security_compliance":
            return [
                f"在 {file_path} 的入口先增加显式权限判断，未通过时立刻返回受控失败结果。",
                "把敏感字段访问和业务执行分开，避免先执行后校验。",
                "补充拒绝场景测试，确认越权请求不会继续走到后续逻辑。",
            ]
        if expert_id == "performance_reliability":
            return [
                f"把 {file_path} 中可能耗时的操作拆到独立步骤，补上超时、批量或短路控制。",
                "为失败路径补充回滚/释放逻辑，避免资源泄漏或半成功状态。",
                "补一条慢路径或异常路径测试，确认高负载下仍然可恢复。",
            ]
        if expert_id == "database_analysis":
            return [
                f"把 {file_path} 对应的 schema/migration 改成兼容性优先的两阶段变更，而不是一次性强收敛。",
                "先加默认值/可空兜底或回填步骤，再做非空、索引或约束收紧。",
                "补充回滚脚本和变更验证 SQL，确保线上执行失败时可恢复。",
            ]
        if expert_id == "test_verification":
            return [
                f"围绕 {file_path} 先补一个最小回归测试，锁住这次缺陷触发条件。",
                "再补失败路径和边界条件断言，避免后续重构把问题重新引入。",
                "如果改动跨文件，再补一个集成级测试，验证最终输出没有漂移。",
            ]
        return common_steps

    def _build_code_excerpt(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        expert_id: str,
    ) -> str:
        excerpt = self.diff_excerpt_service.extract_excerpt(subject.unified_diff, file_path, line_start)
        if excerpt:
            return excerpt
        repository_excerpt = self._load_repository_source_excerpt(subject, file_path, line_start)
        if repository_excerpt:
            return repository_excerpt
        return self._build_fallback_code_excerpt(file_path, line_start, expert_id)

    def _subject_cache_token(self, subject: ReviewSubject | dict[str, object]) -> tuple[object, ...]:
        if isinstance(subject, ReviewSubject):
            return (
                str(subject.repo_id or "").strip(),
                str(subject.source_ref or "").strip(),
                str(subject.target_ref or "").strip(),
                tuple(str(item).strip() for item in list(subject.changed_files or []) if str(item).strip()),
                len(str(subject.unified_diff or "")),
            )
        if isinstance(subject, dict):
            return (
                str(subject.get("repo_id") or "").strip(),
                str(subject.get("source_ref") or "").strip(),
                str(subject.get("target_ref") or "").strip(),
                tuple(str(item).strip() for item in list(subject.get("changed_files", []) or []) if str(item).strip()),
                len(str(subject.get("unified_diff") or "")),
            )
        return ("unknown",)

    def _load_repository_source_excerpt(
        self,
        subject: ReviewSubject | dict[str, object],
        file_path: str,
        line_start: int,
        radius: int = 8,
    ) -> str:
        cache_key = (self._subject_cache_token(subject), str(file_path).strip(), int(line_start or 1), int(radius or 8))
        cached = self._source_excerpt_cache.get(cache_key)
        if cached is not None:
            return cached
        runtime = self.runtime_settings_service.get()
        service = self.repository_resolver.build_context_service(runtime, subject)
        if not service.is_ready():
            return ""
        context = service.load_file_context(file_path, max(1, line_start), radius=radius)
        snippet = str(context.get("snippet") or "").strip()
        self._source_excerpt_cache[cache_key] = snippet
        return snippet

    def _build_target_file_full_diff(self, subject: ReviewSubject, file_path: str) -> str:
        cache_key = (self._subject_cache_token(subject), str(file_path).strip())
        cached = self._target_diff_cache.get(cache_key)
        if cached is not None:
            return cached
        full_diff = self.diff_excerpt_service.extract_file_diff(subject.unified_diff, file_path)
        if not full_diff:
            result = f"未从完整 diff 中提取到 {file_path} 的文件级变更，请结合目标 hunk 和代码仓上下文谨慎判断。"
            self._target_diff_cache[cache_key] = result
            return result
        lines = full_diff.splitlines()
        result = full_diff if len(lines) <= 160 else "\n".join(lines[:160]) + "\n... [目标文件完整 diff 过长，已截断展示前 160 行]"
        self._target_diff_cache[cache_key] = result
        return result

    def _build_related_diff_summary(self, subject: ReviewSubject, target_file_path: str) -> str:
        cache_key = (self._subject_cache_token(subject), str(target_file_path).strip())
        cached = self._related_diff_cache.get(cache_key)
        if cached is not None:
            return cached
        related_paths = [
            str(path).strip()
            for path in list(subject.changed_files or [])
            if str(path).strip() and str(path).strip() != target_file_path
        ]
        if not related_paths:
            result = "除目标文件外无其他变更文件。"
            self._related_diff_cache[cache_key] = result
            return result
        sections: list[str] = []
        for path in related_paths[:4]:
            full_diff = self.diff_excerpt_service.extract_file_diff(subject.unified_diff, path)
            if not full_diff:
                sections.append(f"# {path}\n未提取到该文件 diff。")
                continue
            preview_lines = full_diff.splitlines()
            display_lines = preview_lines[:24]
            suffix = "\n... [摘要已截断]" if len(preview_lines) > 24 else ""
            sections.append(f"# {path}\n" + "\n".join(display_lines) + suffix)
        remaining = len(related_paths) - min(len(related_paths), 4)
        if remaining > 0:
            sections.append(f"... 其余 {remaining} 个变更文件未展开，请结合 changed_files 和代码仓上下文判断影响范围。")
        result = "\n\n".join(sections)
        self._related_diff_cache[cache_key] = result
        return result

    def _merge_runtime_repository_context(
        self,
        repository_context: dict[str, object],
        runtime_tool_results: list[dict[str, object]],
    ) -> dict[str, object]:
        merged = dict(repository_context or {})
        runtime_repo_context = next(
            (
                item
                for item in runtime_tool_results
                if str(item.get("tool_name") or "") == "repo_context_search"
            ),
            None,
        )
        if not isinstance(runtime_repo_context, dict):
            return merged
        passthrough_keys = {
            "primary_context",
            "related_contexts",
            "related_source_snippets",
            "context_files",
            "matches",
            "symbol_contexts",
            "search_keywords",
            "search_keyword_sources",
            "search_commands",
            "definition_hits",
            "reference_hits",
            "symbol_match_strategy",
            "symbol_match_explanation",
            "java_review_mode",
            "java_context_signals",
            "java_quality_signals",
            "java_quality_signal_summary",
            "current_class_context",
            "parent_contract_contexts",
            "caller_contexts",
            "callee_contexts",
            "domain_model_contexts",
            "transaction_context",
            "persistence_contexts",
        }
        for key in passthrough_keys:
            value = runtime_repo_context.get(key)
            if value in (None, "", [], {}):
                continue
            merged[key] = value
        return merged

    def _ensure_repository_context_minimum(
        self,
        *,
        review: ReviewTask,
        repository_context: dict[str, object],
        batch_items: list[dict[str, object]],
        fallback_file_path: str,
        fallback_line_start: int,
        fallback_target_hunk: dict[str, object] | None,
    ) -> dict[str, object]:
        """为专家执行兜底补齐最小可审查上下文，降低“让用户自行核对”的不确定输出。"""

        merged = dict(repository_context or {})
        primary_context = merged.get("primary_context")
        primary_snippet = ""
        if isinstance(primary_context, dict):
            primary_snippet = str(primary_context.get("snippet") or "").strip()
        target_hunk = dict(fallback_target_hunk or {})
        hunk_excerpt = str(target_hunk.get("excerpt") or "").strip()
        if hunk_excerpt and not str(merged.get("target_hunk_excerpt") or "").strip():
            merged["target_hunk_excerpt"] = hunk_excerpt
        if not primary_snippet:
            problem_context = self._load_repository_problem_context(
                review.subject,
                fallback_file_path,
                fallback_line_start,
                target_hunk,
            )
            snippet = str(problem_context.get("snippet") or "").strip()
            if snippet:
                merged["primary_context"] = dict(problem_context)
                primary_snippet = snippet
        if not primary_snippet:
            source_excerpt = self._load_repository_source_excerpt(
                review.subject,
                fallback_file_path,
                fallback_line_start,
            ).strip()
            if source_excerpt:
                merged["primary_context"] = {
                    "path": fallback_file_path,
                    "snippet": source_excerpt,
                    "line_start": int(fallback_line_start or 1),
                }
                primary_snippet = source_excerpt

        if not primary_snippet and hunk_excerpt:
            merged["primary_context"] = {
                "path": fallback_file_path,
                "snippet": hunk_excerpt,
                "line_start": int(
                    self._normalize_optional_line_value(target_hunk.get("start_line"))
                    or fallback_line_start
                    or 1
                ),
            }
            primary_snippet = hunk_excerpt

        if not isinstance(merged.get("current_class_context"), dict):
            merged["current_class_context"] = {}
        current_class_context = dict(merged.get("current_class_context") or {})
        if not str(current_class_context.get("snippet") or "").strip():
            primary = dict(merged.get("primary_context") or {})
            if str(primary.get("snippet") or "").strip():
                merged["current_class_context"] = {
                    "path": str(primary.get("path") or fallback_file_path),
                    "snippet": str(primary.get("snippet") or "").strip(),
                    "line_start": int(primary.get("line_start") or fallback_line_start or 1),
                }

        related_contexts = [
            dict(item)
            for item in list(merged.get("related_contexts") or [])
            if isinstance(item, dict) and str(item.get("snippet") or "").strip()
        ]
        if not related_contexts:
            for item in batch_items:
                item_file_path = str(item.get("file_path") or "").strip()
                if not item_file_path or item_file_path == fallback_file_path:
                    continue
                item_target_hunk = dict(item.get("target_hunk") or {})
                item_line_start = int(item.get("line_start") or 1)
                context = self._load_repository_problem_context(
                    review.subject,
                    item_file_path,
                    item_line_start,
                    item_target_hunk,
                )
                if str(context.get("snippet") or "").strip():
                    related_contexts.append(dict(context))
                if len(related_contexts) >= 3:
                    break
        if related_contexts:
            merged["related_contexts"] = related_contexts

        context_files = [
            str(item).strip()
            for item in list(merged.get("context_files") or [])
            if str(item).strip()
        ]
        for item in [merged.get("primary_context"), *related_contexts]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if path and path not in context_files:
                context_files.append(path)
        if context_files:
            merged["context_files"] = context_files[:16]
        if target_hunk and not isinstance(merged.get("target_hunk"), dict):
            merged["target_hunk"] = dict(target_hunk)
        return merged

    def _context_item_has_snippet(self, item: object) -> bool:
        if not isinstance(item, dict):
            return False
        for key in ("snippet", "excerpt", "content"):
            if str(item.get(key) or "").strip():
                return True
        return False

    def _extract_missing_required_context_sections(self, input_completeness: dict[str, object]) -> list[str]:
        missing_sections = [
            str(item).strip()
            for item in list(input_completeness.get("missing_sections") or [])
            if str(item).strip()
        ]
        required = {"变更代码原文", "当前源码上下文", "关联源码上下文"}
        return [item for item in missing_sections if item in required]

    def _build_finding_code_context(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object],
        repository_context: dict[str, object],
        *,
        expert: ExpertProfile | None = None,
        bound_documents: list[object] | None = None,
        rule_screening: dict[str, object] | None = None,
    ) -> dict[str, object]:
        language = self._infer_code_language(file_path)
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=self._build_target_file_full_diff(subject, file_path),
        )
        enriched_repository_context = dict(repository_context)
        if dict(java_quality.get("analysis_stages") or {}):
            enriched_repository_context["analysis_stages"] = dict(java_quality.get("analysis_stages") or {})
        enriched_repository_context["changed_files"] = [
            str(item).strip()
            for item in list(subject.changed_files or [])[:20]
            if str(item).strip()
        ]
        input_completeness = self._build_review_input_completeness(
            subject,
            file_path,
            line_start,
            enriched_repository_context,
            expert=expert,
            bound_documents=bound_documents or [],
            rule_screening=rule_screening or {},
            language=language,
        )
        return {
            "target_file_full_diff": self._build_target_file_full_diff(subject, file_path),
            "related_diff_summary": self._build_related_diff_summary(subject, file_path),
            "source_file_context": self._load_repository_source_excerpt(subject, file_path, line_start),
            "problem_source_context": self._load_repository_problem_context(subject, file_path, line_start, target_hunk),
            "target_hunk": {
                "file_path": str(target_hunk.get("file_path") or file_path),
                "hunk_header": str(target_hunk.get("hunk_header") or ""),
                "start_line": self._normalize_optional_line_value(target_hunk.get("start_line")) or line_start,
                "end_line": self._normalize_optional_line_value(target_hunk.get("end_line")) or line_start,
                "changed_lines": self._normalize_changed_line_values(target_hunk.get("changed_lines")),
                "excerpt": str(target_hunk.get("excerpt") or ""),
            },
            "primary_context": dict(repository_context.get("primary_context") or {})
            if isinstance(repository_context.get("primary_context"), dict)
            else {},
            "related_contexts": [
                dict(item)
                for item in list(repository_context.get("related_contexts") or [])[:6]
                if isinstance(item, dict)
            ],
            "related_source_snippets": [
                dict(item)
                for item in list(repository_context.get("related_source_snippets") or [])[:6]
                if isinstance(item, dict)
            ],
            "java_review_mode": str(repository_context.get("java_review_mode") or "").strip(),
            "java_context_signals": [
                str(item).strip()
                for item in list(repository_context.get("java_context_signals") or [])[:10]
                if str(item).strip()
            ],
            "java_quality_signals": [
                str(item).strip()
                for item in list(java_quality.get("signals") or [])[:10]
                if str(item).strip()
            ],
            "java_quality_signal_summary": str(java_quality.get("summary") or "").strip(),
            "review_observations": self._normalize_review_observations(java_quality.get("observations")),
            "current_class_context": dict(repository_context.get("current_class_context") or {})
            if isinstance(repository_context.get("current_class_context"), dict)
            else {},
            "parent_contract_contexts": [
                dict(item)
                for item in list(repository_context.get("parent_contract_contexts") or [])[:4]
                if isinstance(item, dict)
            ],
            "caller_contexts": [
                dict(item)
                for item in list(repository_context.get("caller_contexts") or [])[:4]
                if isinstance(item, dict)
            ],
            "callee_contexts": [
                dict(item)
                for item in list(repository_context.get("callee_contexts") or [])[:4]
                if isinstance(item, dict)
            ],
            "domain_model_contexts": [
                dict(item)
                for item in list(repository_context.get("domain_model_contexts") or [])[:4]
                if isinstance(item, dict)
            ],
            "transaction_context": dict(repository_context.get("transaction_context") or {})
            if isinstance(repository_context.get("transaction_context"), dict)
            else {},
            "persistence_contexts": [
                dict(item)
                for item in list(repository_context.get("persistence_contexts") or [])[:4]
                if isinstance(item, dict)
            ],
            "symbol_contexts": [
                dict(item)
                for item in list(repository_context.get("symbol_contexts") or [])[:4]
                if isinstance(item, dict)
            ],
            "code_graph_source_summary": dict(repository_context.get("code_graph_context_source_summary") or {})
            if isinstance(repository_context.get("code_graph_context_source_summary"), dict)
            else {},
            "code_graph_minimal_context": dict(repository_context.get("code_graph_minimal_context") or {})
            if isinstance(repository_context.get("code_graph_minimal_context"), dict)
            else {},
            "code_graph_impact_analysis": dict(repository_context.get("code_graph_impact_analysis") or {})
            if isinstance(repository_context.get("code_graph_impact_analysis"), dict)
            else {},
            "code_graph_related_contexts": [
                dict(item)
                for item in list(repository_context.get("code_graph_related_contexts") or [])[:6]
                if isinstance(item, dict)
            ],
            "code_graph_evidence_chain": self._build_code_graph_evidence_chain(
                file_path=file_path,
                line_start=line_start,
                repository_context=repository_context,
            ),
            "context_files": [
                str(item).strip()
                for item in list(repository_context.get("context_files") or [])[:10]
                if str(item).strip()
            ],
            "changed_files": [
                str(item).strip()
                for item in list(subject.changed_files or [])[:20]
                if str(item).strip()
            ],
            "routing_reason": str(repository_context.get("routing_reason") or "").strip(),
            "input_completeness": input_completeness,
            "review_inputs": self._build_review_input_trace(
                expert=expert,
                bound_documents=bound_documents or [],
                rule_screening=rule_screening or {},
                repository_context=enriched_repository_context,
                language=language,
            ),
        }

    def _build_code_graph_evidence_chain(
        self,
        *,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
    ) -> list[dict[str, object]]:
        source_summary = dict(repository_context.get("code_graph_context_source_summary") or {})
        minimal_context = dict(repository_context.get("code_graph_minimal_context") or {})
        impact = dict(repository_context.get("code_graph_impact_analysis") or {})
        related_contexts = [
            dict(item)
            for item in list(repository_context.get("code_graph_related_contexts") or [])[:6]
            if isinstance(item, dict)
        ]
        if not (source_summary or minimal_context or impact or related_contexts):
            return []
        normalized_file = str(file_path or "").strip().replace("\\", "/")
        changed_nodes = [
            dict(item)
            for item in list(impact.get("changed_nodes") or [])[:6]
            if isinstance(item, dict)
        ]
        review_priorities = [
            dict(item)
            for item in list(minimal_context.get("review_priorities") or impact.get("review_priorities") or [])[:6]
            if isinstance(item, dict)
        ]
        affected_flows = [
            dict(item)
            for item in list(minimal_context.get("affected_flows") or impact.get("affected_flows") or [])[:6]
            if isinstance(item, dict)
        ]
        matching_contexts = [
            item
            for item in related_contexts
            if not normalized_file or str(item.get("path") or item.get("file_path") or "").replace("\\", "/") == normalized_file
        ] or related_contexts[:3]
        return [
            {
                "step": "context_source",
                "status": str(source_summary.get("primary_source") or "tree_sitter"),
                "primary_source": str(source_summary.get("primary_source") or ""),
                "fallback_used": bool(source_summary.get("fallback_used")),
                "fallback_reason": str(source_summary.get("fallback_reason") or ""),
            },
            {
                "step": "minimal_context",
                "status": "present" if minimal_context else "missing",
                "summary": str(minimal_context.get("summary") or impact.get("summary") or ""),
                "risk_level": str(minimal_context.get("risk_level") or impact.get("risk_level") or ""),
                "risk_score": float(minimal_context.get("risk_score") or impact.get("risk_score") or 0.0),
                "key_entities": list(minimal_context.get("key_entities") or [])[:5],
            },
            {
                "step": "changed_node",
                "status": "anchored" if changed_nodes else "missing",
                "file_path": normalized_file,
                "line_start": int(line_start or 1),
                "nodes": changed_nodes,
            },
            {
                "step": "graph_relationship",
                "status": "matched" if matching_contexts else "missing",
                "contexts": [
                    {
                        "relationship": str(item.get("relationship") or ""),
                        "path": str(item.get("path") or item.get("file_path") or ""),
                        "line_number": int(item.get("line_number") or item.get("line_start") or 0),
                        "source": str(item.get("source_qualified_name") or ""),
                        "target": str(item.get("target_qualified_name") or ""),
                        "context_source": str(item.get("context_source") or "tree_sitter"),
                    }
                    for item in matching_contexts[:4]
                ],
            },
            {
                "step": "review_priority",
                "status": "ranked" if review_priorities else "missing",
                "priorities": review_priorities[:5],
            },
            {
                "step": "affected_flows",
                "status": "matched" if affected_flows else "missing",
                "flows": affected_flows[:5],
            },
        ]

    def _finding_context_source(self, code_context: dict[str, object]) -> str:
        source_summary = dict(code_context.get("code_graph_source_summary") or {})
        primary_source = str(source_summary.get("primary_source") or "").strip()
        if primary_source:
            return primary_source
        if list(code_context.get("code_graph_related_contexts") or []):
            return "tree_sitter"
        if list(code_context.get("related_contexts") or []):
            return "repository_context"
        return "diff"

    def _build_finding_evidence_chain(self, finding: ReviewFinding) -> list[dict[str, object]]:
        code_context = dict(finding.code_context or {})
        chain: list[dict[str, object]] = [
            {
                "step": "claim",
                "status": "present" if finding.summary or finding.title else "missing",
                "claim": finding.summary or finding.title,
                "finding_id": finding.finding_id,
            },
            {
                "step": "diff_anchor",
                "status": "anchored" if finding.file_path and finding.line_start else "missing",
                "file_path": finding.file_path,
                "line_start": int(finding.line_start or 1),
                "evidence": list(finding.evidence or [])[:4],
            },
        ]
        graph_chain = [
            dict(item)
            for item in list(code_context.get("code_graph_evidence_chain") or [])
            if isinstance(item, dict)
        ]
        chain.extend(graph_chain)
        observation_ids = [
            str(item).strip()
            for item in list(code_context.get("observation_ids") or [])
            if str(item).strip()
        ]
        if observation_ids:
            chain.append(
                {
                    "step": "static_observation",
                    "status": "matched",
                    "observation_ids": observation_ids[:8],
                }
            )
        sast_matches = [
            dict(item)
            for item in list(code_context.get("sast_prescan_matches") or [])
            if isinstance(item, dict)
        ]
        if sast_matches:
            chain.append(
                {
                    "step": "sast_prescan",
                    "status": "matched",
                    "matches": sast_matches[:5],
                }
            )
        chain.append(
            {
                "step": "context_source",
                "status": self._finding_context_source(code_context),
                "context_source": self._finding_context_source(code_context),
            }
        )
        return chain

    def _load_repository_problem_context(
        self,
        subject: ReviewSubject | dict[str, object],
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object],
    ) -> dict[str, object]:
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if changed_lines:
            start_line = min(changed_lines)
            end_line = max(changed_lines)
        else:
            start_line = self._normalize_optional_line_value(target_hunk.get("start_line")) or line_start
            end_line = self._normalize_optional_line_value(target_hunk.get("end_line")) or start_line
        padding = self._compute_problem_context_padding(start_line, end_line, changed_lines)
        cache_key = (
            self._subject_cache_token(subject),
            str(file_path).strip(),
            int(line_start or 1),
            int(start_line or 1),
            int(end_line or start_line or 1),
            tuple(changed_lines),
        )
        cached = self._problem_context_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        runtime = self.runtime_settings_service.get()
        service = self.repository_resolver.build_context_service(runtime, subject)
        if not service.is_ready():
            return {}
        context = service.load_file_range(
            file_path,
            start_line,
            end_line,
            padding=padding,
            expand_to_block=True,
        )
        result = dict(context) if isinstance(context, dict) else {}
        self._problem_context_cache[cache_key] = dict(result)
        return result

    def _compute_problem_context_padding(
        self,
        start_line: int,
        end_line: int,
        changed_lines: list[int],
    ) -> int:
        """为问题代码区域计算更完整的源码窗口。

        结果页里的“当前代码”不应该只覆盖问题点附近几行。这里按问题跨度自适应放大窗口：
        - 单点/短 hunk：优先给出更大的上下文，尽量覆盖完整方法或代码块
        - 中等 hunk：保持足够多的上下文辅助判断上下游逻辑
        - 超长 hunk：仍限制在可读范围内，避免结果页过长
        """

        normalized_start = max(1, int(start_line or 1))
        normalized_end = max(normalized_start, int(end_line or normalized_start))
        changed_count = len(changed_lines)
        span = max(1, normalized_end - normalized_start + 1, changed_count)

        if span <= 3:
            return 18
        if span <= 8:
            return 16
        if span <= 16:
            return 14
        if span <= 28:
            return 12
        return 10

    def _build_fallback_code_excerpt(
        self,
        file_path: str,
        line_start: int,
        expert_id: str,
    ) -> str:
        language = self._infer_code_language(file_path)
        if language == "java":
            lines = [
                f"{line_start:>4} | public void process(Request request) {{",
                f"{line_start + 1:>4} |     if (request == null) {{ return; }}",
                f"{line_start + 2:>4} |     repository.save(request.toEntity());",
                f"{line_start + 3:>4} | }}",
            ]
        elif language == "typescript" or language == "javascript":
            lines = [
                f"{line_start:>4} | function process(input) {{",
                f"{line_start + 1:>4} |   if (!input) return;",
                f"{line_start + 2:>4} |   return service.save(input);",
                f"{line_start + 3:>4} | }}",
            ]
        elif language == "sql":
            lines = [
                f"{line_start:>4} | BEGIN;",
                f"{line_start + 1:>4} | UPDATE target_table",
                f"{line_start + 2:>4} | SET updated_at = NOW()",
                f"{line_start + 3:>4} | WHERE id = ?;",
            ]
        else:
            lines = [
                f"{line_start:>4} | # fallback excerpt for unavailable repository context",
                f"{line_start + 1:>4} | # expert={expert_id}",
                f"{line_start + 2:>4} | # please verify against real source in repository",
                f"{line_start + 3:>4} | pass",
            ]
        return f"# {file_path}\n" + "\n".join(lines)

    def _build_suggested_code(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        expert_id: str,
    ) -> str:
        language = self._infer_code_language(file_path)
        if language == "sql":
            return (
                f"-- Suggested fix for {file_path}\n"
                "BEGIN;\n"
                "-- Step 1: add compatible defaults first\n"
                "ALTER TABLE target_table\n"
                "  ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT NOW(),\n"
                "  ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT NOW();\n\n"
                "-- Step 2: backfill historical rows before tightening constraints\n"
                "UPDATE target_table\n"
                "SET updated_at = COALESCE(updated_at, created_at, NOW())\n"
                "WHERE updated_at IS NULL;\n\n"
                "COMMIT;"
            )
        if language == "prisma":
            return (
                "model ExampleEntity {\n"
                "  id         Int      @id @default(autoincrement())\n"
                "  createdAt  DateTime @default(now())\n"
                "  updatedAt  DateTime @updatedAt\n"
                "}\n"
            )
        if expert_id == "test_verification":
            if language in {"typescript", "tsx", "javascript", "jsx"}:
                return (
                    "describe(\"review flow\", () => {\n"
                    "  it(\"rejects invalid input\", async () => {\n"
                    "    const result = await executeReview({ enabled: false });\n"
                    "    expect(result.allowed).toBe(false);\n"
                    "  });\n\n"
                    "  it(\"keeps the success path stable\", async () => {\n"
                    "    const result = await executeReview({ enabled: true });\n"
                    "    expect(result.allowed).toBe(true);\n"
                    "  });\n"
                    "});"
                )
            return (
                "def test_review_guard_rejects_invalid_payload():\n"
                "    assert review_guard({\"enabled\": False}, user=build_user()) is False\n\n"
                "def test_review_guard_allows_valid_payload():\n"
                "    assert review_guard({\"enabled\": True}, user=build_user(can_review=True)) is True\n"
            )
        if language in {"typescript", "tsx", "javascript", "jsx"}:
            if expert_id == "security_compliance":
                return (
                    "export function reviewGuard(payload: ReviewPayload, currentUser: CurrentUser): boolean {\n"
                    "  if (!currentUser.permissions.includes(\"review:write\")) {\n"
                    "    return false;\n"
                    "  }\n\n"
                    "  if (!payload.enabled) {\n"
                    "    return false;\n"
                    "  }\n\n"
                    "  return true;\n"
                    "}\n"
                )
            if expert_id == "architecture_design":
                return (
                    "const REVIEW_ENABLED = true;\n\n"
                    "function ensureTraceId(traceId?: string): string {\n"
                    "  if (!traceId) {\n"
                    "    throw new Error(\"traceId is required\");\n"
                    "  }\n"
                    "  return traceId;\n"
                    "}\n\n"
                    "export function reviewGuard(payload: ReviewPayload, logger: Logger): boolean {\n"
                    "  const traceId = ensureTraceId(payload.traceId);\n"
                    "  logger.info(\"review_guard\", { traceId, enabled: payload.enabled });\n"
                    "  return REVIEW_ENABLED && Boolean(payload.enabled);\n"
                    "}\n"
                )
            if expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
                return (
                    "export function createCourse(command: CreateCourseCommand, deps: { repository: CourseRepository, eventBus: EventBus }): Course {\n"
                    "  const course = Course.create(command.id, command.name, command.duration);\n"
                    "  deps.repository.save(course);\n"
                    "  deps.eventBus.publish(course.pullDomainEvents());\n"
                    "  return course;\n"
                    "}\n"
                )
            return (
                "export function reviewGuard(payload: ReviewPayload): boolean {\n"
                "  const enabled = Boolean(payload.enabled);\n"
                "  if (!enabled) {\n"
                "    return false;\n"
                "  }\n\n"
                "  return true;\n"
                "}\n"
            )
        if language == "python":
            if expert_id == "security_compliance":
                return (
                    "def review_guard(payload: dict, user: User) -> bool:\n"
                    "    if not user.can(\"review:write\"):\n"
                    "        return False\n"
                    "    if not payload.get(\"enabled\"):\n"
                    "        return False\n"
                    "    return True\n"
                )
            if expert_id == "architecture_design":
                return (
                    "REVIEW_ENABLED = True\n\n"
                    "def ensure_trace_id(payload: dict) -> str:\n"
                    "    trace_id = str(payload.get(\"trace_id\") or \"\").strip()\n"
                    "    if not trace_id:\n"
                    "        raise ValueError(\"trace_id is required\")\n"
                    "    return trace_id\n\n"
                    "def review_guard(payload: dict, logger: Logger) -> bool:\n"
                    "    trace_id = ensure_trace_id(payload)\n"
                    "    logger.info(\"review_guard\", extra={\"trace_id\": trace_id, \"enabled\": payload.get(\"enabled\")})\n"
                    "    return REVIEW_ENABLED and bool(payload.get(\"enabled\"))\n"
                )
            if expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
                return (
                    "def create_course(command: CreateCourseCommand, repository: CourseRepository, event_bus: EventBus) -> Course:\n"
                    "    course = Course.create(command.id, command.name, command.duration)\n"
                    "    repository.save(course)\n"
                    "    event_bus.publish(course.pull_domain_events())\n"
                    "    return course\n"
                )
            return (
                "def review_guard(payload: dict) -> bool:\n"
                    "    enabled = bool(payload.get(\"enabled\"))\n"
                "    if not enabled:\n"
                "        return False\n"
                "    return True\n"
            )
        return (
            f"# Suggested rewrite for {file_path}\n"
            "# 1. Separate validation from execution\n"
            "# 2. Return early on invalid input\n"
            "# 3. Keep the happy path flat and testable\n"
        )

    def _infer_code_language(self, file_path: str) -> str:
        lowered = file_path.lower()
        if lowered.endswith(".tsx"):
            return "tsx"
        if lowered.endswith(".ts"):
            return "typescript"
        if lowered.endswith(".jsx"):
            return "jsx"
        if lowered.endswith(".js"):
            return "javascript"
        if lowered.endswith(".py"):
            return "python"
        if lowered.endswith(".sql"):
            return "sql"
        if lowered.endswith(".prisma"):
            return "prisma"
        if lowered.endswith(".java"):
            return "java"
        if lowered.endswith(".go"):
            return "go"
        return "text"

    def _build_language_general_guidance(self, language: str) -> str:
        normalized = str(language or "").strip().lower()
        if normalized == "java":
            return (
                "- 以《阿里巴巴 Java 开发手册》作为 Java 代码最低通用规范基线，再叠加当前产品/专家绑定的规范文档一起审查。\n"
                "- 遵循 Java / Spring 通用代码规范：命名清晰，单个方法职责收敛，避免把校验、事务、持久化、远程调用混成一个长方法，避免使用 tmp/data/value 这类弱语义命名。\n"
                "- 关注输入校验、空值处理、异常边界、日志脱敏、权限/租户隔离，以及 @Transactional 范围内的副作用。\n"
                "- 检查循环体内的 Repository / Service / Client / HTTP / SQL / MQ 调用，识别 N+1、逐条远程调用、批量场景串行放大和数据库往返放大风险。\n"
                "- 检查 Repository / JPA / MyBatis 查询是否存在无分页、全表扫描、N+1、批量逐条写、EAGER/级联加载风险。\n"
                "- 检查条件分支、状态码、重试次数、批量阈值、字符串标识等是否以魔法值形式直接散落在业务逻辑中，是否应提取为常量、枚举或配置。\n"
                "- 检查注释、TODO、方法名、接口说明承诺的行为是否真的落地；如果只留下说明、占位或半截逻辑，要明确指出“承诺未实现/承诺与实现不一致”。\n"
                "- 若结论依赖调用链、ORM 映射或事务传播，必须结合已提供源码上下文和工具证据；证据不足的条目不要输出。"
            )
        if normalized in {"javascript", "jsx", "typescript", "tsx"}:
            return (
                "- 遵循 JavaScript / TypeScript 通用代码规范：命名清晰，避免隐藏副作用，保持函数职责单一，异步流程要显式处理错误和资源释放。\n"
                "- 关注输入校验、鉴权边界、日志与敏感信息暴露、空值/undefined 处理、Promise/await 错误传播和并发竞态。\n"
                "- 检查数据库/HTTP/缓存调用是否存在无边界重试、批量串行、未分页查询、未取消请求或阻塞主路径的问题。\n"
                "- 若结论依赖运行时分支、类型收窄或框架约定，必须引用已提供代码证据；证据不足的条目不要输出。"
            )
        return ""






























































    def _build_expert_fallback(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
    ) -> str:
        summary = self._build_finding_summary(subject, expert.expert_id)
        return (
            f"回应主Agent：收到，我先看 {file_path}:{line_start} 附近的改动。\n"
            f"问题标题：{self._build_finding_title(expert)}\n"
            f"风险结论：从{expert.name_zh}视角看，这里最值得警惕的是：{summary}\n"
            f"代码证据：我已经基于 diff 片段、绑定运行时工具和知识库命中结果完成首轮取证，但仍需补充更直接的上下文证据。\n"
            f"修复建议：{self._build_remediation_suggestion(subject, expert.expert_id, file_path)}\n"
            f"证据诉求：需要补充关联测试、失败路径和变更前后的行为对比。"
        )









    def _build_knowledge_review_context(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
        target_hunk: dict[str, object],
    ) -> dict[str, object]:
        """构造专家知识库章节召回使用的上下文。"""

        query_terms: list[str] = [file_path, expert.role, expert.expert_id]
        for value in [
            target_hunk.get("hunk_header"),
            target_hunk.get("excerpt"),
            repository_context.get("routing_reason"),
            repository_context.get("symbol_query"),
            repository_context.get("primary_symbol"),
        ]:
            if isinstance(value, str) and value.strip():
                query_terms.append(value.strip())
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=self._build_target_file_full_diff(subject, file_path),
        )
        java_review_mode = str(repository_context.get("java_review_mode") or "").strip()
        if java_review_mode:
            query_terms.append(f"java_mode:{java_review_mode}")
        for signal in list(repository_context.get("java_context_signals") or [])[:8]:
            normalized = str(signal).strip()
            if normalized:
                query_terms.append(f"java_signal:{normalized}")
        is_java_file = Path(str(file_path or "")).suffix.lower() == ".java"
        quality_prefix = "java_quality" if is_java_file else "quality"
        term_prefix = "java_term" if is_java_file else "quality_term"
        for signal in list(java_quality.get("signals") or [])[:8]:
            normalized = str(signal).strip()
            if normalized:
                query_terms.append(f"{quality_prefix}:{normalized}")
        for term in list(java_quality.get("matched_terms") or [])[:8]:
            normalized = str(term).strip()
            if normalized:
                query_terms.append(f"{term_prefix}:{normalized}")
        repo_instruction_payload = repository_context.get("repo_review_instructions")
        if isinstance(repo_instruction_payload, dict):
            for instruction in list(repo_instruction_payload.get("instructions") or [])[:6]:
                if not isinstance(instruction, dict):
                    continue
                for value in [
                    instruction.get("title"),
                    instruction.get("content"),
                    " ".join(str(item) for item in list(instruction.get("matched_globs") or [])[:3]),
                ]:
                    normalized = str(value or "").strip()
                    if normalized:
                        query_terms.append(f"repo_instruction:{normalized[:240]}")
        return {
            "changed_files": list(subject.changed_files),
            "query_terms": query_terms,
            "knowledge_sources": list(expert.knowledge_sources or []),
            "focus_file": file_path,
            "focus_line": line_start,
        }






    def _build_bound_document_metadata(self, bound_documents: list[object]) -> list[dict[str, object]]:
        """把绑定文档裁成前端友好的结构化摘要，避免过程页再次展示原始 JSON。"""

        summaries: list[dict[str, object]] = []
        for item in bound_documents[:6]:
            title = str(getattr(item, "title", "") or "").strip()
            if not title:
                continue
            outline = [
                str(value).strip()
                for value in list(getattr(item, "indexed_outline", []) or [])[:10]
                if str(value).strip()
            ]
            matched_sections: list[dict[str, object]] = []
            for section in list(getattr(item, "matched_sections", []) or [])[:4]:
                path = str(getattr(section, "path", "") or getattr(section, "title", "") or "").strip()
                summary = str(getattr(section, "summary", "") or "").strip()
                content = str(getattr(section, "content", "") or "").strip()
                snippet = summary or (content.splitlines()[0].strip() if content else "")
                matched_sections.append(
                    {
                        "path": path,
                        "summary": snippet,
                        "score": round(float(getattr(section, "score", 0.0) or 0.0), 3),
                        "matched_terms": [
                            str(term).strip()
                            for term in list(getattr(section, "matched_terms", []) or [])[:8]
                            if str(term).strip()
                        ],
                        "matched_signals": [
                            str(signal).strip()
                            for signal in list(getattr(section, "matched_signals", []) or [])[:8]
                            if str(signal).strip()
                        ],
                    }
                )
            summaries.append(
                {
                    "doc_id": str(getattr(item, "doc_id", "") or "").strip(),
                    "title": title,
                    "doc_type": str(getattr(item, "doc_type", "") or "").strip(),
                    "source_filename": str(getattr(item, "source_filename", "") or "").strip(),
                    "indexed_outline": outline,
                    "matched_sections": matched_sections,
                }
            )
        return summaries

    def _build_rule_screening_metadata(self, rule_screening: dict[str, object]) -> dict[str, object]:
        return {
            "total_rules": int(rule_screening.get("total_rules") or 0),
            "enabled_rules": int(rule_screening.get("enabled_rules") or 0),
            "must_review_count": int(rule_screening.get("must_review_count") or 0),
            "possible_hit_count": int(rule_screening.get("possible_hit_count") or 0),
            "matched_rule_count": int(rule_screening.get("matched_rule_count") or 0),
            "screening_mode": str(rule_screening.get("screening_mode") or "").strip(),
            "screening_fallback_used": bool(rule_screening.get("screening_fallback_used")),
            "total_elapsed_ms": round(float(rule_screening.get("total_elapsed_ms") or 0.0), 2),
            "batch_count": len(list(rule_screening.get("batch_summaries", []) or [])),
            "matched_rules_for_llm": [
                {
                    "rule_id": str(item.get("rule_id") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "priority": str(item.get("priority") or "").strip(),
                    "decision": str(item.get("decision") or "").strip(),
                    "reason": str(item.get("reason") or "").strip(),
                    "matched_terms": [
                        str(value).strip()
                        for value in list(item.get("matched_terms", []) or [])[:8]
                        if str(value).strip()
                    ],
                }
                for item in list(rule_screening.get("matched_rules_for_llm", []) or [])[:6]
                if str(item.get("rule_id") or item.get("title") or "").strip()
            ],
        }

    def _build_repository_context_metadata(self, repository_context: dict[str, object]) -> dict[str, object]:
        """为过程页保留紧凑的代码仓上下文摘要，避免消息里重复落整份源码片段。"""

        if not repository_context:
            return {}
        payload: dict[str, object] = {
            "summary": str(repository_context.get("summary") or "").strip(),
            "routing_reason": str(repository_context.get("routing_reason") or "").strip(),
            "java_review_mode": str(repository_context.get("java_review_mode") or "").strip(),
            "java_context_signals": [
                str(item).strip()
                for item in list(repository_context.get("java_context_signals") or [])[:8]
                if str(item).strip()
            ],
            "java_quality_signals": [
                str(item).strip()
                for item in list(repository_context.get("java_quality_signals") or [])[:8]
                if str(item).strip()
            ],
            "java_quality_signal_summary": str(repository_context.get("java_quality_signal_summary") or "").strip(),
            "context_files": [
                str(item).strip()
                for item in list(repository_context.get("context_files") or [])[:8]
                if str(item).strip()
            ],
            "cross_file_impact_hints": build_cross_file_impact_hints(
                file_path=str((repository_context.get("primary_context") or {}).get("path") or ""),
                repository_context=repository_context,
            ),
            "prompt_graph_facts": build_prompt_graph_facts(repository_context),
        }

        def _compact_entries(key: str, *, symbol_key: str = "symbol") -> list[dict[str, object]]:
            return [
                {
                    "path": str(item.get("path") or "").strip(),
                    "symbol": str(item.get(symbol_key) or item.get("class_name") or "").strip(),
                    "line_start": int(item.get("line_start") or 0) if item.get("line_start") else 0,
                }
                for item in list(repository_context.get(key) or [])[:4]
                if isinstance(item, dict) and str(item.get("path") or item.get(symbol_key) or item.get("class_name") or "").strip()
            ]

        primary_context = repository_context.get("primary_context")
        if isinstance(primary_context, dict):
            payload["primary_context"] = {
                "path": str(primary_context.get("path") or "").strip(),
                "line_start": int(primary_context.get("line_start") or 0) if primary_context.get("line_start") else 0,
            }

        current_class_context = repository_context.get("current_class_context")
        if isinstance(current_class_context, dict):
            payload["current_class_context"] = {
                "path": str(current_class_context.get("path") or "").strip(),
                "symbol": str(current_class_context.get("symbol") or current_class_context.get("class_name") or "").strip(),
                "line_start": int(current_class_context.get("line_start") or 0)
                if current_class_context.get("line_start")
                else 0,
            }

        for key in (
            "related_contexts",
            "related_source_snippets",
            "caller_contexts",
            "callee_contexts",
            "domain_model_contexts",
            "persistence_contexts",
        ):
            entries = _compact_entries(key)
            if entries:
                payload[key] = entries

        transaction_context = repository_context.get("transaction_context")
        if isinstance(transaction_context, dict):
            payload["transaction_context"] = {
                "transactional_method": str(transaction_context.get("transactional_method") or "").strip(),
                "transactional_path": str(transaction_context.get("transactional_path") or "").strip(),
                "call_chain": [
                    str(item).strip()
                    for item in list(transaction_context.get("call_chain") or [])[:8]
                    if str(item).strip()
                ],
            }

        return {key: value for key, value in payload.items() if value not in (None, "", [], {}, 0)}

    def _build_tool_result_metadata(self, tool_result: dict[str, object]) -> dict[str, object]:
        payload = {
            "tool_name": str(tool_result.get("tool_name") or "").strip(),
            "summary": str(tool_result.get("summary") or "").strip(),
            "skipped": bool(tool_result.get("skipped")),
            "skip_reason": str(tool_result.get("skip_reason") or "").strip(),
            "signal_summary": str(
                tool_result.get("signal_summary") or tool_result.get("java_quality_signal_summary") or ""
            ).strip(),
            "signals": [
                str(item).strip()
                for item in list(tool_result.get("signals") or tool_result.get("java_quality_signals") or [])[:8]
                if str(item).strip()
            ],
            "context_files": [
                str(item).strip()
                for item in list(tool_result.get("context_files") or [])[:6]
                if str(item).strip()
            ],
            "data_source_summary": dict(tool_result.get("data_source_summary") or {}),
            "matched_tables": [
                str(item).strip()
                for item in list(tool_result.get("matched_tables") or [])[:8]
                if str(item).strip()
            ],
            "table_columns": [
                dict(item)
                for item in list(tool_result.get("table_columns") or [])[:12]
                if isinstance(item, dict)
            ],
            "constraints": [
                dict(item)
                for item in list(tool_result.get("constraints") or [])[:12]
                if isinstance(item, dict)
            ],
            "indexes": [
                dict(item)
                for item in list(tool_result.get("indexes") or [])[:12]
                if isinstance(item, dict)
            ],
            "table_stats": [
                dict(item)
                for item in list(tool_result.get("table_stats") or [])[:8]
                if isinstance(item, dict)
            ],
        }
        return {key: value for key, value in payload.items() if value not in (None, "", [], {})}

    def _build_runtime_tool_results_metadata(self, runtime_tool_results: list[dict[str, object]]) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for item in runtime_tool_results[:8]:
            if not isinstance(item, dict):
                continue
            compact = self._build_tool_result_metadata(item)
            if compact:
                results.append(compact)
        return results

    def _build_rule_screening_batch_messages(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        rule_screening: dict[str, object],
        runtime_settings: RuntimeSettings,
    ) -> list[ConversationMessage]:
        batches = list(rule_screening.get("batch_summaries", []) or [])
        if not batches:
            return []
        messages: list[ConversationMessage] = []
        screening_mode = str(rule_screening.get("screening_mode") or "").strip() or "heuristic"
        fallback_used = bool(rule_screening.get("screening_fallback_used"))
        for raw_batch in batches:
            if not isinstance(raw_batch, dict):
                continue
            batch_llm_metadata = self._build_rule_screening_batch_llm_metadata(raw_batch)
            batch_index = int(raw_batch.get("batch_index") or 0)
            batch_count = int(raw_batch.get("batch_count") or 0)
            input_rule_count = int(raw_batch.get("input_rule_count") or 0)
            must_review_count = int(raw_batch.get("must_review_count") or 0)
            possible_hit_count = int(raw_batch.get("possible_hit_count") or 0)
            no_hit_count = int(raw_batch.get("no_hit_count") or 0)
            selected_count = must_review_count + possible_hit_count
            mode_label = "LLM" if screening_mode == "llm" else "启发式"
            fallback_note = "，已回退启发式" if fallback_used else ""
            content = (
                f"规则筛选第 {batch_index}/{batch_count} 批已完成："
                f"输入 {input_rule_count} 条规则，带入审查 {selected_count} 条"
                f"（{mode_label}{fallback_note}）。"
            )
            messages.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_rule_screening_batch",
                    content=content,
                    metadata={
                        "phase": "coordination",
                        "file_path": file_path,
                        "line_start": line_start,
                        "rule_screening_total_elapsed_ms": round(float(rule_screening.get("total_elapsed_ms") or 0.0), 2),
                        "rule_screening_batch": self._build_rule_screening_batch_metadata(raw_batch),
                        "rule_screening": self._build_rule_screening_metadata(rule_screening),
                        **(batch_llm_metadata or self._expert_llm_metadata(expert, runtime_settings)),
                    },
                )
            )
        return messages

    def _build_rule_screening_batch_llm_metadata(self, batch: dict[str, object]) -> dict[str, object]:
        llm = batch.get("llm")
        if not isinstance(llm, dict):
            return {}
        return {
            "llm_call_id": str(llm.get("llm_call_id") or "").strip(),
            "provider": str(llm.get("provider") or "").strip(),
            "model": str(llm.get("model") or "").strip(),
            "base_url": str(llm.get("base_url") or "").strip(),
            "api_key_env": str(llm.get("api_key_env") or "").strip(),
            "mode": str(llm.get("mode") or "").strip(),
            "llm_error": str(llm.get("llm_error") or "").strip(),
            "elapsed_ms": round(float(llm.get("elapsed_ms") or 0.0), 2),
            "prompt_tokens": int(llm.get("prompt_tokens") or 0),
            "completion_tokens": int(llm.get("completion_tokens") or 0),
            "total_tokens": int(llm.get("total_tokens") or 0),
        }

    def _build_rule_screening_batch_metadata(self, batch: dict[str, object]) -> dict[str, object]:
        decisions = []
        for item in list(batch.get("decisions", []) or [])[:24]:
            if not isinstance(item, dict):
                continue
            decisions.append(
                {
                    "rule_id": str(item.get("rule_id") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "priority": str(item.get("priority") or "").strip(),
                    "decision": str(item.get("decision") or "").strip(),
                    "reason": str(item.get("reason") or "").strip(),
                    "matched_terms": [
                        str(value).strip()
                        for value in list(item.get("matched_terms", []) or [])[:8]
                        if str(value).strip()
                    ],
                    "matched_signals": [
                        str(value).strip()
                        for value in list(item.get("matched_signals", []) or [])[:8]
                        if str(value).strip()
                    ],
                }
            )
        return {
            "batch_index": int(batch.get("batch_index") or 0),
            "batch_count": int(batch.get("batch_count") or 0),
            "screening_mode": str(batch.get("screening_mode") or "").strip(),
            **self._build_rule_screening_batch_llm_metadata(batch),
            "input_rule_count": int(batch.get("input_rule_count") or 0),
            "must_review_count": int(batch.get("must_review_count") or 0),
            "possible_hit_count": int(batch.get("possible_hit_count") or 0),
            "no_hit_count": int(batch.get("no_hit_count") or 0),
            "input_rules": [
                {
                    "rule_id": str(item.get("rule_id") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "priority": str(item.get("priority") or "").strip(),
                }
                for item in list(batch.get("input_rules", []) or [])[:24]
                if isinstance(item, dict) and str(item.get("rule_id") or item.get("title") or "").strip()
            ],
            "decisions": decisions,
        }

    def _build_knowledge_context_metadata(self, knowledge_context: dict[str, object]) -> dict[str, object]:
        """裁剪知识检索上下文，供过程页展示诊断信息。"""

        return {
            "focus_file": str(knowledge_context.get("focus_file") or "").strip(),
            "focus_line": int(knowledge_context.get("focus_line") or 0) if knowledge_context.get("focus_line") else 0,
            "changed_files": [
                str(item).strip()
                for item in list(knowledge_context.get("changed_files", []) or [])[:8]
                if str(item).strip()
            ],
            "query_terms": [
                str(item).strip()
                for item in list(knowledge_context.get("query_terms", []) or [])[:12]
                if str(item).strip()
            ],
            "knowledge_sources": [
                str(item).strip()
                for item in list(knowledge_context.get("knowledge_sources", []) or [])[:8]
                if str(item).strip()
            ],
        }

    def _build_hunk_summary(self, target_hunk: dict[str, object]) -> str:
        if not target_hunk:
            return "未定位到明确 hunk，请结合当前代码片段谨慎判断。"
        header = str(target_hunk.get("hunk_header") or "").strip()
        excerpt = str(target_hunk.get("excerpt") or "").strip()
        lines = []
        if header:
            lines.append(header)
        if excerpt:
            excerpt_lines = excerpt.splitlines()
            lines.extend(excerpt_lines[:8])
        return "\n".join(lines) if lines else "未定位到明确 hunk，请结合当前代码片段谨慎判断。"

    def _build_hunk_batch_summary(self, target_hunks: list[dict[str, object]]) -> str:
        normalized = [dict(item) for item in list(target_hunks or []) if isinstance(item, dict)]
        if len(normalized) <= 1:
            return "当前文件仅有一个重点 hunk。"
        sections: list[str] = []
        for index, item in enumerate(normalized[:8], start=1):
            header = str(item.get("hunk_header") or "").strip()
            start_line = self._normalize_optional_line_value(item.get("start_line")) or self._normalize_optional_line_value(item.get("line_start")) or 1
            excerpt = str(item.get("excerpt") or "").strip()
            sections.append(f"{index}. L{start_line} {header}".strip())
            if excerpt:
                sections.extend(f"   {line}" for line in excerpt.splitlines()[:4])
        if len(normalized) > 8:
            sections.append(f"... 其余 {len(normalized) - 8} 个 hunk 未展开，但仍属于本次同文件联合审查范围。")
        return "\n".join(sections)

    def _line_in_target_hunks(self, line_start: int, target_hunks: list[dict[str, object]] | None) -> bool:
        normalized_line = int(line_start or 0)
        if normalized_line <= 0:
            return False
        normalized_hunks = [dict(item) for item in list(target_hunks or []) if isinstance(item, dict)]
        if not normalized_hunks:
            return True
        for hunk in normalized_hunks:
            changed_lines = self._normalize_changed_line_values(hunk.get("changed_lines"))
            if changed_lines:
                if normalized_line in changed_lines:
                    return True
                continue
            start_line = (
                self._normalize_optional_line_value(hunk.get("start_line"))
                or self._normalize_optional_line_value(hunk.get("line_start"))
            )
            end_line = self._normalize_optional_line_value(hunk.get("end_line")) or start_line
            if start_line is not None and end_line is not None and start_line <= normalized_line <= end_line:
                return True
        return False

    def _target_hunk_anchor_line(self, target_hunk: dict[str, object], *, fallback: int) -> int:
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if changed_lines:
            return min(changed_lines)
        start_line = (
            self._normalize_optional_line_value(target_hunk.get("start_line"))
            or self._normalize_optional_line_value(target_hunk.get("line_start"))
        )
        return int(start_line or fallback or 1)

    def _finding_has_valid_diff_anchor(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object] | None = None,
    ) -> bool:
        """Only allow formal findings anchored to post-change lines in the MR diff."""

        if not str(subject.unified_diff or "").strip():
            return True
        normalized_file = str(file_path or "").strip().replace("\\", "/")
        if not normalized_file:
            return False
        normalized_line = int(line_start or 0)
        if normalized_line <= 0:
            return False
        hunk_file = str((target_hunk or {}).get("file_path") or "").strip().replace("\\", "/")
        if hunk_file and hunk_file != normalized_file:
            return False
        hunk_changed_lines = self._normalize_changed_line_values((target_hunk or {}).get("changed_lines"))
        if hunk_changed_lines:
            return normalized_line in set(hunk_changed_lines)
        changed_lines = set(self.diff_excerpt_service.changed_line_numbers(subject.unified_diff, normalized_file))
        return normalized_line in changed_lines

    def _normalize_finding_rule_attribution(
        self,
        parsed: dict[str, object],
        *,
        rule_screening: dict[str, object],
        expert_id: str,
    ) -> dict[str, object]:
        """Separate general expert norms from additive product/repo rule cards.

        Product/repo rules enrich the review, but they are not a gate for formal
        findings. We only police fabricated rule-card IDs so the UI can show
        reliable attribution without suppressing valid general expert issues.
        """

        available_rules: dict[str, dict[str, object]] = {}
        available_titles: dict[str, dict[str, object]] = {}
        for item in list((rule_screening or {}).get("matched_rules_for_llm") or []):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or "").strip()
            title = str(item.get("title") or "").strip()
            if rule_id:
                available_rules[rule_id.upper()] = dict(item)
            if title:
                available_titles[title.lower()] = dict(item)

        raw_rules = self._normalize_text_list(parsed.get("matched_rules"), [])
        general_rules: list[str] = []
        valid_custom_rule_ids: list[str] = []
        invalid_custom_rule_ids: list[str] = []
        custom_rule_details: list[dict[str, object]] = []

        for rule in raw_rules:
            rule_text = str(rule or "").strip()
            if not rule_text:
                continue
            extracted_ids = self._extract_additive_rule_ids(rule_text)
            matched_known_ids = [rule_id for rule_id in extracted_ids if rule_id.upper() in available_rules]
            if matched_known_ids:
                for rule_id in matched_known_ids:
                    canonical = str(available_rules[rule_id.upper()].get("rule_id") or rule_id).strip()
                    if canonical and canonical not in valid_custom_rule_ids:
                        valid_custom_rule_ids.append(canonical)
                        custom_rule_details.append(self._compact_rule_detail(available_rules[rule_id.upper()]))
                residual = rule_text
                for rule_id in matched_known_ids:
                    residual = residual.replace(rule_id, "").strip(" :：,，;；-")
                if residual:
                    general_rules.append(residual)
                continue
            if extracted_ids:
                invalid_custom_rule_ids.extend(
                    rule_id
                    for rule_id in extracted_ids
                    if rule_id.upper() not in available_rules
                )
                # Keep any non-ID explanatory text as a general guideline if present.
                residual = rule_text
                for rule_id in extracted_ids:
                    residual = residual.replace(rule_id, "").strip(" :：,，;；-")
                if residual:
                    general_rules.append(residual)
                continue
            title_match = available_titles.get(rule_text.lower())
            if title_match:
                canonical = str(title_match.get("rule_id") or rule_text).strip()
                if canonical and canonical not in valid_custom_rule_ids:
                    valid_custom_rule_ids.append(canonical)
                    custom_rule_details.append(self._compact_rule_detail(title_match))
                continue
            general_rules.append(rule_text)

        normalized_matched_rules = self._dedupe_texts([*general_rules, *valid_custom_rule_ids])
        assumptions: list[str] = []
        if invalid_custom_rule_ids:
            assumptions.append(
                "专家引用了本轮规则遍历结果中不存在的附加规则 ID，系统已移除该附加规则引用；该问题仍按专家通用规范和代码证据独立判断。"
            )

        sources: list[str] = []
        if general_rules or (not normalized_matched_rules and (parsed.get("evidence") or parsed.get("cross_file_evidence"))):
            sources.append("expert_general")
        if valid_custom_rule_ids:
            sources.append("product_or_repo_custom")
        if not sources:
            sources.append("unattributed")

        return {
            "expert_id": str(expert_id or "").strip(),
            "sources": sources,
            "general_rules": self._dedupe_texts(general_rules),
            "valid_custom_rule_ids": valid_custom_rule_ids,
            "invalid_custom_rule_ids": self._dedupe_texts(invalid_custom_rule_ids),
            "custom_rule_details": custom_rule_details,
            "available_custom_rule_ids": [
                str(item.get("rule_id") or "").strip()
                for item in list((rule_screening or {}).get("matched_rules_for_llm") or [])[:12]
                if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
            ],
            "normalized_matched_rules": normalized_matched_rules,
            "custom_rules_are_additive": True,
            "assumptions": assumptions,
        }

    def _extract_additive_rule_ids(self, text: str) -> list[str]:
        raw = str(text or "")
        if not raw:
            return []
        # Rule-card IDs in this project are normally uppercase, hyphenated and
        # contain a numeric suffix, e.g. SEC-JAVA-001 or ORDER-AUTH-002.
        candidates = re.findall(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{2,}\b", raw)
        return self._dedupe_texts(candidates)

    def _compact_rule_detail(self, rule: dict[str, object]) -> dict[str, object]:
        return {
            "rule_id": str(rule.get("rule_id") or "").strip(),
            "title": str(rule.get("title") or "").strip(),
            "priority": str(rule.get("priority") or "").strip(),
            "scene_path": str(rule.get("scene_path") or "").strip(),
            "reason": str(rule.get("reason") or "").strip(),
        }

    def _apply_additive_rule_priority_to_severity(
        self,
        severity: str,
        *,
        finding_type: str,
        rule_attribution: dict[str, object],
    ) -> str:
        if str(finding_type or "").strip().lower() not in {"direct_defect", "direct_code_issue"}:
            return severity
        details = [dict(item) for item in list(rule_attribution.get("custom_rule_details") or []) if isinstance(item, dict)]
        priorities = {str(item.get("priority") or "").strip().upper() for item in details}
        current = str(severity or "medium").strip().lower()
        if priorities & {"P0", "BLOCKER"} and current not in {"blocker", "critical", "high"}:
            return "high"
        if priorities & {"P1", "HIGH"} and current in {"low", "medium"}:
            return "high"
        return severity

    def _finding_matches_current_diff_code(
        self,
        parsed: dict[str, object],
        target_hunk: dict[str, object] | None,
    ) -> dict[str, object]:
        """Reject findings whose concrete code anchors only exist in removed lines.

        The line gate above proves the finding is attached to a post-change line.
        This semantic gate proves the claim itself is not about code that the MR
        has already deleted or replaced.
        """

        hunk_lines = self._parse_target_hunk_diff_lines(target_hunk or {})
        added_lines = [text for _, text in hunk_lines.get("added", []) if str(text).strip()]
        removed_lines = [text for _, text in hunk_lines.get("removed", []) if str(text).strip()]
        if not added_lines or not removed_lines:
            return {"matched": True}

        semantic_parts: list[str] = []
        for key in (
            "title",
            "claim",
            "summary",
            "fix_strategy",
            "suggested_fix",
            "rule_based_reasoning",
            "suggested_code",
        ):
            value = str(parsed.get(key) or "").strip()
            if value:
                semantic_parts.append(value)
        for key in ("evidence", "assumptions", "matched_rules", "violated_guidelines", "change_steps"):
            semantic_parts.extend(str(item).strip() for item in list(parsed.get(key) or []) if str(item).strip())

        finding_tokens = self._extract_anchor_tokens("\n".join(semantic_parts))
        if not finding_tokens:
            return {"matched": True}
        generic_tokens = {
            "current",
            "change",
            "changed",
            "diff",
            "issue",
            "problem",
            "method",
            "class",
            "return",
            "public",
            "private",
            "final",
            "static",
            "string",
            "boolean",
            "object",
            "null",
            "true",
            "false",
            "风险",
            "问题",
            "代码",
            "方法",
            "新增",
            "删除",
            "当前",
        }
        finding_tokens = {token for token in finding_tokens if token not in generic_tokens}
        if not finding_tokens:
            return {"matched": True}

        added_tokens = self._extract_anchor_tokens("\n".join(added_lines))
        removed_tokens = self._extract_anchor_tokens("\n".join(removed_lines))
        added_overlap = finding_tokens & added_tokens
        removed_overlap = finding_tokens & removed_tokens
        removed_only_overlap = removed_overlap - added_tokens

        strong_removed_only_overlap = {
            token
            for token in removed_only_overlap
            if len(token) >= 10 or "_" in token or any(char.isdigit() for char in token)
        }
        if strong_removed_only_overlap:
            return {
                "matched": False,
                "added_token_overlap": [],
                "removed_token_overlap": sorted(strong_removed_only_overlap),
                "added_lines": added_lines,
                "removed_lines": removed_lines,
            }
        if added_overlap:
            return {
                "matched": True,
                "added_token_overlap": sorted(added_overlap),
                "removed_token_overlap": sorted(removed_overlap),
                "added_lines": added_lines,
                "removed_lines": removed_lines,
            }
        if removed_only_overlap:
            return {
                "matched": False,
                "added_token_overlap": [],
                "removed_token_overlap": sorted(removed_only_overlap),
                "added_lines": added_lines,
                "removed_lines": removed_lines,
            }
        return {"matched": True}

    def _parse_target_hunk_diff_lines(self, target_hunk: dict[str, object]) -> dict[str, list[tuple[int | None, str]]]:
        excerpt = str((target_hunk or {}).get("excerpt") or "")
        if not excerpt:
            return {"added": [], "removed": [], "context": []}

        parsed: dict[str, list[tuple[int | None, str]]] = {"added": [], "removed": [], "context": []}
        changed_lines = self._normalize_changed_line_values((target_hunk or {}).get("changed_lines"))
        changed_index = 0
        for raw_line in excerpt.splitlines():
            if not raw_line.strip() or raw_line.startswith("#"):
                continue
            formatted = re.match(r"^\s*(\d+)\s+\|\s*([+\- ])(.*)$", raw_line)
            if formatted:
                line_no = int(formatted.group(1))
                marker = formatted.group(2)
                text = formatted.group(3).strip()
            else:
                removed_formatted = re.match(r"^\s*-\s+\|\s*(.*)$", raw_line)
                if removed_formatted:
                    line_no = None
                    marker = "-"
                    text = removed_formatted.group(1).strip()
                elif raw_line.startswith("+") and not raw_line.startswith("+++"):
                    line_no = changed_lines[min(changed_index, len(changed_lines) - 1)] if changed_lines else None
                    marker = "+"
                    text = raw_line[1:].strip()
                elif raw_line.startswith("-") and not raw_line.startswith("---"):
                    line_no = None
                    marker = "-"
                    text = raw_line[1:].strip()
                else:
                    continue

            if marker == "+":
                parsed["added"].append((line_no, text))
                if changed_index < len(changed_lines) - 1:
                    changed_index += 1
            elif marker == "-":
                parsed["removed"].append((line_no, text))
            else:
                parsed["context"].append((line_no, text))
        return parsed

    def _match_target_hunk_for_line(
        self,
        line_start: int,
        target_hunk: dict[str, object],
        target_hunks: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        normalized_hunks = [dict(item) for item in list(target_hunks or []) if isinstance(item, dict)]
        if not normalized_hunks:
            return dict(target_hunk or {})
        for item in normalized_hunks:
            changed_lines = self._normalize_changed_line_values(item.get("changed_lines"))
            if changed_lines and line_start in changed_lines:
                return item
            start_line = self._normalize_optional_line_value(item.get("start_line")) or line_start
            end_line = self._normalize_optional_line_value(item.get("end_line")) or start_line
            if start_line <= line_start <= end_line:
                return item
        return dict(target_hunk or normalized_hunks[0])

    def _resolve_finding_target_hunk(
        self,
        parsed: dict[str, object],
        *,
        fallback_line_start: int,
        target_hunk: dict[str, object],
        target_hunks: list[dict[str, object]] | None = None,
        used_hunk_line_starts: set[int] | None = None,
    ) -> dict[str, object]:
        normalized_hunks = [dict(item) for item in list(target_hunks or []) if isinstance(item, dict)]
        if not normalized_hunks:
            return dict(target_hunk or {})

        explicit_line_start = self._normalize_optional_line_value(parsed.get("line_start"))
        if explicit_line_start is not None and explicit_line_start != fallback_line_start:
            return self._match_target_hunk_for_line(explicit_line_start, target_hunk, normalized_hunks)

        semantic_hunk = self._match_target_hunk_by_semantics(parsed, normalized_hunks)
        if semantic_hunk is not None:
            return semantic_hunk
        if used_hunk_line_starts is not None and len(normalized_hunks) > 1:
            for item in normalized_hunks:
                start_line = (
                    self._normalize_optional_line_value(item.get("start_line"))
                    or self._normalize_optional_line_value(item.get("line_start"))
                    or 0
                )
                if start_line > 0 and start_line not in used_hunk_line_starts:
                    return dict(item)
        if explicit_line_start is not None:
            return self._match_target_hunk_for_line(explicit_line_start, target_hunk, normalized_hunks)
        return dict(target_hunk or normalized_hunks[0])

    def _match_target_hunk_by_semantics(
        self,
        parsed: dict[str, object],
        target_hunks: list[dict[str, object]],
    ) -> dict[str, object] | None:
        if len(target_hunks) <= 1:
            return None

        semantic_parts: list[str] = []
        for key in (
            "title",
            "claim",
            "summary",
            "fix_strategy",
            "suggested_fix",
            "rule_based_reasoning",
        ):
            value = str(parsed.get(key) or "").strip()
            if value:
                semantic_parts.append(value)
        for key in ("evidence", "assumptions", "matched_rules", "violated_guidelines", "change_steps"):
            semantic_parts.extend(str(item).strip() for item in list(parsed.get(key) or []) if str(item).strip())

        finding_tokens = self._extract_anchor_tokens("\n".join(semantic_parts))
        if not finding_tokens:
            return None

        best_hunk: dict[str, object] | None = None
        best_score = 0
        second_best_score = 0
        excerpt_phrases = [str(parsed.get("title") or "").strip(), str(parsed.get("claim") or "").strip()]
        for hunk in target_hunks:
            hunk_text = "\n".join(
                [
                    str(hunk.get("hunk_header") or "").strip(),
                    str(hunk.get("excerpt") or "").strip(),
                ]
            )
            hunk_tokens = self._extract_anchor_tokens(hunk_text)
            overlap = finding_tokens & hunk_tokens
            score = 0
            for token in overlap:
                score += 3 if len(token) >= 8 or any(char.isdigit() for char in token) else 1
            excerpt_lower = str(hunk.get("excerpt") or "").lower()
            for phrase in excerpt_phrases:
                normalized_phrase = phrase.lower()
                if normalized_phrase and len(normalized_phrase) >= 6 and normalized_phrase in excerpt_lower:
                    score += 4
            if score > best_score:
                second_best_score = best_score
                best_score = score
                best_hunk = dict(hunk)
            elif score > second_best_score:
                second_best_score = score

        if best_hunk is None or best_score <= 0:
            return None
        if second_best_score and best_score == second_best_score:
            return None
        return best_hunk

    def _extract_anchor_tokens(self, text: str) -> set[str]:
        if not text:
            return set()
        stopwords = {
            "this",
            "that",
            "with",
            "from",
            "have",
            "should",
            "would",
            "could",
            "into",
            "when",
            "where",
            "then",
            "line",
            "file",
            "code",
            "rule",
            "must",
            "need",
            "using",
            "java",
        }
        tokens: set[str] = set()
        for raw_token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", text):
            normalized = raw_token.strip("_").lower()
            if len(normalized) >= 3 and normalized not in stopwords:
                tokens.add(normalized)
            for part in re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|_|$)|[A-Z]?[a-z]+|\d+", raw_token):
                normalized_part = part.strip("_").lower()
                if len(normalized_part) >= 3 and normalized_part not in stopwords:
                    tokens.add(normalized_part)
        return tokens

    def _merge_context_files(
        self,
        parsed_context_files: object,
        repository_context: dict[str, object],
        runtime_tool_results: list[dict[str, object]],
    ) -> list[str]:
        """合并多处来源的上下文文件，并过滤无意义路径。"""
        merged: list[str] = []
        for item in list(parsed_context_files or []):
            text = str(item).strip()
            if text and self._is_meaningful_context_file(text) and text not in merged:
                merged.append(text)
        for item in list(repository_context.get("context_files", []) or []):
            text = str(item).strip()
            if text and self._is_meaningful_context_file(text) and text not in merged:
                merged.append(text)
        for result in runtime_tool_results:
            if str(result.get("tool_name") or "") != "repo_context_search":
                continue
            for item in list(result.get("context_files", []) or []):
                text = str(item).strip()
                if text and self._is_meaningful_context_file(text) and text not in merged:
                    merged.append(text)
        return merged[:6]

    def _is_test_like_path(self, path: str) -> bool:
        normalized = Path(str(path or "").replace("\\", "/"))
        parts = normalized.parts
        if any(part.lower() in {"test", "tests", "__tests__", "__mocks__", "spec", "specs", "fixtures", "playwright", "cypress"} for part in parts):
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

    def _business_changed_files(self, subject: ReviewSubject) -> list[str]:
        business_files = [
            item
            for item in subject.changed_files
            if item and not self._is_test_like_path(item)
        ]
        return business_files or [item for item in subject.changed_files if item]

    def _extract_design_alignment(self, runtime_tool_results: list[dict[str, object]]) -> dict[str, object]:
        """从 design_spec_alignment tool 结果里提取设计一致性信息。"""
        for item in runtime_tool_results:
            if str(item.get("tool_name") or "") != "design_spec_alignment":
                continue
            if bool(item.get("skipped")) and str(item.get("skip_reason") or "") == "design_docs_missing":
                continue
            if not self._normalize_text_list(item.get("design_doc_titles"), []):
                continue
            return dict(item)
        return {}

    def _emit_skill_summary_messages(
        self,
        review: ReviewTask,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        active_skills: list[object],
        runtime_tool_results: list[dict[str, object]],
        target_hunk: dict[str, object],
        runtime_settings,
        target_hunks: list[dict[str, object]] | None = None,
    ) -> None:
        """把关键 skill 的执行结果转成更适合人看的专家消息。

        tool 调用消息偏“过程取证”，这里额外补一条专家视角摘要，
        帮用户快速看懂：专家到底从详细设计里解析出了什么。
        """
        skill_ids = {str(getattr(skill, "skill_id", "") or "") for skill in active_skills}
        design_alignment = self._extract_design_alignment(runtime_tool_results)
        if expert.expert_id != "correctness_business":
            return
        if "design-consistency-check" not in skill_ids:
            return
        if not design_alignment:
            return

        summary = self._build_design_skill_summary(design_alignment)
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_skill_call",
                content=summary["content"],
                metadata={
                    "phase": "expert_review",
                    "skill_name": "design-consistency-check",
                    "file_path": file_path,
                    "line_start": line_start,
                    "skill_result": summary["skill_result"],
                    "design_alignment_status": design_alignment.get("design_alignment_status", ""),
                    "design_doc_titles": design_alignment.get("design_doc_titles", []),
                    "target_hunk": target_hunk,
                    "target_hunks": [dict(item) for item in list(target_hunks or []) if isinstance(item, dict)][:8],
                    **self._expert_llm_metadata(expert, runtime_settings),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="expert_skill_invoked",
                phase="expert_review",
                message=f"{expert.name_zh} 已输出详细设计解析摘要",
                payload={
                    "expert_id": expert.expert_id,
                    "skill_name": "design-consistency-check",
                    "design_alignment_status": design_alignment.get("design_alignment_status", ""),
                    "design_doc_titles": design_alignment.get("design_doc_titles", []),
                },
            )
        )

    def _auto_confirm_high_confidence_issues(self, issues: list[DebateIssue]) -> tuple[list[DebateIssue], list[str]]:
        """Allow strong direct-evidence issues to skip the human gate.

        The issue still remains in the formal issue list; this only prevents
        high-confidence, well-anchored defects from blocking the whole review
        behind manual confirmation.
        """

        confirmed_ids: list[str] = []
        updated: list[DebateIssue] = []
        for issue in issues:
            next_issue = issue.model_copy(deep=True)
            if self._can_auto_confirm_issue(next_issue):
                next_issue.needs_human = False
                next_issue.status = "resolved"
                next_issue.resolution = "auto_confirmed_direct_evidence"
                next_issue.verified = True
                next_issue.updated_at = datetime.now(UTC)
                confirmed_ids.append(next_issue.issue_id)
            updated.append(next_issue)
        return updated, confirmed_ids

    def _can_auto_confirm_issue(self, issue: DebateIssue) -> bool:
        if not issue.needs_human or issue.status == "resolved":
            return False
        if float(issue.confidence or 0.0) < 0.92:
            return False
        if len(issue.evidence_chain or []) < 2:
            return False
        if not (issue.direct_evidence or issue.verified or issue.tool_verified or issue.sast_cross_validated):
            return False
        if issue.consistency_conflicts or issue.remediation_alignment_conflicts:
            return False
        if issue.remediation_filtered:
            return False
        blocked_statuses = {"downgraded", "validator_failed"}
        if str(issue.consistency_check_status or "").strip().lower() in blocked_statuses:
            return False
        if str(issue.resolution or "").strip().lower() in {
            "consistency_validation_failed",
            "human_rejected",
            "llm_judge_rejected",
        }:
            return False
        return True

    def _build_design_skill_summary(self, design_alignment: dict[str, object]) -> dict[str, object]:
        """把 design_spec_alignment 结果压成对话流可读摘要。"""
        structured = dict(design_alignment.get("structured_design") or {})
        design_doc_titles = self._normalize_text_list(design_alignment.get("design_doc_titles", []), [])
        business_goal = str(structured.get("business_goal") or "").strip()
        api_definitions = self._format_design_api_definitions(structured.get("api_definitions", []))
        request_fields = self._format_design_fields(structured.get("request_fields", []))
        response_fields = self._format_design_fields(structured.get("response_fields", []))
        table_definitions = self._format_design_tables(structured.get("table_definitions", []))
        business_sequences = self._format_design_sequences(structured.get("business_sequences", []))
        performance_requirements = self._format_design_requirements(structured.get("performance_requirements", []))
        security_requirements = self._format_design_requirements(structured.get("security_requirements", []))
        ambiguous_points = self._normalize_text_list(structured.get("unknown_or_ambiguous_points", []), [])
        matched_points = self._normalize_text_list(design_alignment.get("matched_implementation_points", []), [])
        missing_points = self._normalize_text_list(design_alignment.get("missing_implementation_points", []), [])
        conflict_points = self._normalize_text_list(design_alignment.get("conflicting_implementation_points", []), [])
        uncertain_points = self._normalize_text_list(design_alignment.get("uncertain_points", []), [])
        status = str(design_alignment.get("design_alignment_status") or "").strip() or "insufficient_design_context"
        status_label = {
            "aligned": "设计一致",
            "partially_aligned": "部分偏离设计",
            "misaligned": "与设计冲突",
            "insufficient_design_context": "设计上下文不足",
        }.get(status, status)
        content = (
            f"已完成详细设计解析：{status_label}。"
            f" 共识别 {len(api_definitions)} 个 API 定义、{len(response_fields)} 个关键出参字段、"
            f"{len(table_definitions)} 组表结构定义、{len(business_sequences)} 条业务时序要点。"
        )
        skill_result = {
            "summary": content,
            "design_doc_titles": design_doc_titles,
            "design_alignment_status": status,
            "business_goal": business_goal,
            "api_definitions": api_definitions[:4],
            "request_fields": request_fields[:6],
            "response_fields": response_fields[:6],
            "table_definitions": table_definitions[:4],
            "business_sequences": business_sequences[:5],
            "performance_requirements": performance_requirements[:4],
            "security_requirements": security_requirements[:4],
            "unknown_or_ambiguous_points": ambiguous_points[:5],
            "matched_design_points": matched_points[:5],
            "missing_design_points": missing_points[:5],
            "design_conflicts": conflict_points[:5],
            "uncertain_points": uncertain_points[:5],
        }
        return {"content": content, "skill_result": skill_result}

    def _format_design_api_definitions(self, value: object) -> list[str]:
        lines: list[str] = []
        for item in list(value or []):
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    lines.append(text)
                continue
            method = str(item.get("method") or "").strip()
            path = str(item.get("path") or "").strip()
            purpose = str(item.get("purpose") or "").strip()
            line = " ".join(part for part in [method, path] if part).strip()
            if purpose:
                line = f"{line} · {purpose}" if line else purpose
            if line:
                lines.append(line)
        return lines

    def _format_design_fields(self, value: object) -> list[str]:
        lines: list[str] = []
        for item in list(value or []):
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    lines.append(text)
                continue
            name = str(item.get("name") or "").strip()
            location = str(item.get("location") or "").strip()
            field_type = str(item.get("field_type") or "").strip()
            required = str(item.get("required") or "").strip()
            description = str(item.get("description") or "").strip()
            head = name or "未命名字段"
            if field_type:
                head += f": {field_type}"
            extras = [item for item in [location, required, description] if item]
            line = f"{head} · {' · '.join(extras)}" if extras else head
            lines.append(line)
        return lines

    def _format_design_tables(self, value: object) -> list[str]:
        lines: list[str] = []
        for item in list(value or []):
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    lines.append(text)
                continue
            table_name = str(item.get("table_name") or "").strip() or "未命名表"
            fields = self._normalize_text_list(item.get("fields"), [])
            constraints = self._normalize_text_list(item.get("constraints"), [])
            indexes = self._normalize_text_list(item.get("indexes"), [])
            extras: list[str] = []
            if fields:
                extras.append(f"字段: {', '.join(fields[:4])}")
            if constraints:
                extras.append(f"约束: {', '.join(constraints[:3])}")
            if indexes:
                extras.append(f"索引: {', '.join(indexes[:3])}")
            lines.append(f"{table_name} · {' · '.join(extras)}" if extras else table_name)
        return lines

    def _format_design_sequences(self, value: object) -> list[str]:
        lines: list[str] = []
        for item in list(value or []):
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    lines.append(text)
                continue
            step = str(item.get("step") or "").strip()
            actor = str(item.get("actor") or "").strip()
            action = str(item.get("action") or "").strip()
            expected = str(item.get("expected_result") or "").strip()
            line = " -> ".join(part for part in [actor, action, expected] if part).strip()
            if step:
                line = f"{step}. {line}" if line else step
            if line:
                lines.append(line)
        return lines

    def _format_design_requirements(self, value: object) -> list[str]:
        lines: list[str] = []
        for item in list(value or []):
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    lines.append(text)
                continue
            title = str(item.get("title") or "").strip()
            requirement = str(item.get("requirement") or "").strip()
            line = f"{title} · {requirement}" if title and requirement else title or requirement
            if line:
                lines.append(line)
        return lines
