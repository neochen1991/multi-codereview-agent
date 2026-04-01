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
from app.repositories.sqlite_event_repository import SqliteEventRepository
from app.repositories.sqlite_finding_repository import SqliteFindingRepository
from app.repositories.sqlite_issue_repository import SqliteIssueRepository
from app.repositories.sqlite_message_repository import SqliteMessageRepository
from app.repositories.sqlite_review_repository import SqliteReviewRepository
from app.services.artifact_service import ArtifactService, build_report_summary
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.expert_capability_service import ExpertCapabilityService
from app.services.expert_registry import ExpertRegistry
from app.services.java_quality_signal_extractor import JavaQualitySignalExtractor
from app.services.knowledge_service import KnowledgeService
from app.services.llm_chat_service import LLMChatService
from app.services.main_agent_service import MainAgentService
from app.services.memory_probe import MemoryProbe
from app.services.orchestrator.graph import build_review_graph
from app.services.repository_context_service import RepositoryContextService
from app.services.review_skill_activation_service import ReviewSkillActivationService
from app.services.review_skill_registry import ReviewSkillRegistry
from app.services.runtime_settings_service import RuntimeSettingsService
from app.services.tool_gateway import ReviewToolGateway

logger = logging.getLogger(__name__)

FALLBACK_EXPERT_ID = "architecture_design"


class ReviewClosedError(RuntimeError):
    """表示审核任务被用户主动关闭，应立即停止后续执行。"""


class ReviewRunner:
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
        db_path = self._resolve_db_path(self.storage_root)
        self.review_repo = SqliteReviewRepository(db_path)
        self.event_repo = SqliteEventRepository(db_path)
        self.finding_repo = SqliteFindingRepository(db_path)
        self.issue_repo = SqliteIssueRepository(db_path)
        self.message_repo = SqliteMessageRepository(db_path)
        self.registry = ExpertRegistry(self.storage_root / "experts")
        self.runtime_settings_service = RuntimeSettingsService(self.storage_root)
        self.artifact_service = ArtifactService(self.storage_root)
        self.diff_excerpt_service = DiffExcerptService()
        self.capability_service = ExpertCapabilityService()
        self.main_agent_service = MainAgentService()
        self.llm_chat_service = LLMChatService()
        self.java_quality_signal_extractor = JavaQualitySignalExtractor()
        self.review_tool_gateway = ReviewToolGateway(self.storage_root)
        self.review_skill_registry = ReviewSkillRegistry(Path(__file__).resolve().parents[3] / "extensions" / "skills")
        self.review_skill_activation_service = ReviewSkillActivationService()
        self.knowledge_service = KnowledgeService(self.storage_root)
        self.knowledge_service.bootstrap_builtin_documents()
        self.graph = build_review_graph()

    def _resolve_db_path(self, root: Path) -> Path:
        """Resolve SQLite path from storage root, honoring global default when unchanged."""

        resolved_root = Path(root).resolve()
        default_storage_root = Path(settings.STORAGE_ROOT).resolve()
        if resolved_root == default_storage_root:
            return Path(settings.SQLITE_DB_PATH)
        return resolved_root / "app.db"

    def bootstrap_demo_review(self) -> str:
        review_id = f"rev_{uuid4().hex[:8]}"
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
                changed_files=["backend/app/main.py"],
                unified_diff=(
                    "diff --git a/backend/app/main.py b/backend/app/main.py\n"
                    "--- a/backend/app/main.py\n"
                    "+++ b/backend/app/main.py\n"
                    "@@ -1,2 +1,3 @@\n"
                    " from fastapi import FastAPI\n"
                    "+from fastapi.middleware.cors import CORSMiddleware\n"
                    " app = FastAPI()\n"
                ),
            ),
            selected_experts=settings.DEFAULT_EXPERT_IDS,
        )
        self.review_repo.save(task)
        return review_id

    def list_events(self, review_id: str) -> list[ReviewEvent]:
        return self.event_repo.list(review_id)

    def run_once(self, review_id: str) -> ReviewTask:
        """完整执行一次审核主链。"""
        review = self.review_repo.get(review_id)
        if review is None:
            raise KeyError(review_id)
        MemoryProbe.log("review_runner.start", review_id=review_id)
        self._abort_if_closed(review_id)

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
        analysis_mode = self._resolve_analysis_mode(review, runtime_settings)
        effective_runtime_settings = self._effective_runtime_settings(runtime_settings, analysis_mode)
        llm_request_options = self._build_llm_request_options(effective_runtime_settings, analysis_mode)
        requested_selected_ids = review.selected_experts or settings.DEFAULT_EXPERT_IDS
        enabled_experts = self.registry.list_enabled()
        selection_started_at = time.perf_counter()
        selection_plan = self.main_agent_service.select_review_experts(
            review.subject,
            enabled_experts,
            effective_runtime_settings,
            requested_expert_ids=requested_selected_ids,
        )
        MemoryProbe.log(
            "review_runner.after_expert_selection",
            review_id=review.review_id,
            selected_expert_count=len(list(selection_plan.get("selected_expert_ids", []) or [])),
        )
        selection_elapsed_ms = round((time.perf_counter() - selection_started_at) * 1000, 1)
        selected_ids = [
            expert_id
            for expert_id in list(selection_plan.get("selected_expert_ids", []) or [])
            if isinstance(expert_id, str) and expert_id.strip()
        ]
        experts = [expert for expert in enabled_experts if expert.expert_id in selected_ids]
        review.selected_experts = selected_ids
        review.subject.metadata = {
            **review.subject.metadata,
            "expert_selection": {
                "requested_expert_ids": list(selection_plan.get("requested_expert_ids", []) or []),
                "candidate_expert_ids": list(selection_plan.get("candidate_expert_ids", []) or []),
                "selected_experts": list(selection_plan.get("selected_experts", []) or []),
                "skipped_experts": list(selection_plan.get("skipped_experts", []) or []),
                "llm": dict(selection_plan.get("llm") or {}),
            },
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
                message="主Agent 已基于 MR 信息和专家画像确定本次参与审核的专家",
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
            [expert.expert_id for expert in experts],
            llm_request_options["timeout_seconds"],
            llm_request_options["max_attempts"],
            self._max_parallel_experts(effective_runtime_settings, analysis_mode),
        )
        if not experts:
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
            review.duration_seconds = max(
                0.0,
                round((review.completed_at - (review.started_at or review.created_at)).total_seconds(), 3),
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

        experts_by_id = {expert.expert_id: expert for expert in experts}
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
        routing_plan = self.main_agent_service.build_routing_plan(
            review.subject,
            experts,
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
        for expert in experts:
            command = self.main_agent_service.build_command(
                review.subject,
                expert,
                effective_runtime_settings,
                route_hint=routing_plan.get(expert.expert_id),
            )
            expert_id = expert.expert_id
            file_path = str(command.get("file_path") or self._pick_file_path(review.subject, expert_id))
            line_start = int(command.get("line_start") or 1)
            summary = str(command.get("summary") or "")
            llm_metadata = dict(command.get("llm") or {})
            if not bool(command.get("routeable", True)):
                skip_reason = str(command.get("skip_reason") or "当前变更未命中该专家的有效审查线索")
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
                        "file_path": file_path,
                        "line_start": line_start,
                        "related_files": command.get("related_files", []),
                        "business_changed_files": self._business_changed_files(review.subject),
                        "target_hunk": command.get("target_hunk", {}),
                        "repository_context": self._build_repository_context_metadata(
                            dict(command.get("repository_context") or {})
                        ),
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
                    message=f"主Agent 已向 {expert.name_zh} 下发审查指令",
                    payload={
                        "target_expert_id": expert_id,
                        "target_expert_name": expert.name_zh,
                        "file_path": file_path,
                        "line_start": line_start,
                        "related_files": command.get("related_files", []),
                        "business_changed_files": self._business_changed_files(review.subject),
                    },
                )
            )
            effective_experts.append(
                {
                    "expert_id": expert_id,
                    "expert_name": expert.name_zh,
                    "source": "user_selected",
                    "file_path": file_path,
                    "line_start": line_start,
                }
            )
            knowledge_context = self._build_knowledge_review_context(
                review.subject,
                expert,
                file_path,
                line_start,
                dict(command.get("repository_context") or {}),
                dict(command.get("target_hunk") or {}),
            )
            bound_documents = self.knowledge_service.retrieve_for_expert(expert.expert_id, knowledge_context)
            rule_screening = self.knowledge_service.screen_rules_for_expert(
                expert.expert_id,
                knowledge_context,
                runtime_settings=effective_runtime_settings,
                analysis_mode=analysis_mode,
                review_id=review.review_id,
            )
            logger.info(
                "expert rule screening prepared review_id=%s expert_id=%s file_path=%s line_start=%s total_rules=%s matched_rule_count=%s must_review=%s possible_hit=%s matched_rule_ids=%s",
                review.review_id,
                expert.expert_id,
                file_path,
                line_start,
                int(rule_screening.get("total_rules") or 0),
                int(rule_screening.get("matched_rule_count") or 0),
                int(rule_screening.get("must_review_count") or 0),
                int(rule_screening.get("possible_hit_count") or 0),
                [
                    str(item.get("rule_id") or "").strip()
                    for item in list(rule_screening.get("matched_rules_for_llm", []) or [])[:8]
                ],
            )
            expert_jobs.append(
                {
                    "review": review,
                    "expert": expert,
                    "command_message": command_message,
                    "file_path": file_path,
                    "line_start": line_start,
                    "repository_context": dict(command.get("repository_context") or {}),
                    "target_hunk": dict(command.get("target_hunk") or {}),
                    "related_files": list(command.get("related_files") or []),
                    "business_changed_files": self._business_changed_files(review.subject),
                    "expected_checks": list(command.get("expected_checks") or []),
                    "disallowed_inference": list(command.get("disallowed_inference") or []),
                    "routing_reason": str(command.get("routing_reason") or ""),
                    "routing_confidence": float(command.get("routing_confidence") or 0.0),
                    "runtime_settings": effective_runtime_settings,
                    "analysis_mode": analysis_mode,
                    "llm_request_options": llm_request_options,
                    "bound_documents": bound_documents,
                    "knowledge_context": knowledge_context,
                    "rule_screening": rule_screening,
                    "finding_payloads": finding_payloads,
                }
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
        expert_failures = self._execute_expert_jobs(expert_jobs, effective_runtime_settings, analysis_mode)
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
        self._abort_if_closed(review_id)
        if not expert_jobs:
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
            review.duration_seconds = max(
                0.0,
                round((review.completed_at - (review.started_at or review.created_at)).total_seconds(), 3),
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
                        getattr(runtime_settings, "issue_min_priority_level", "P2") or "P2"
                    ).upper(),
                    "issue_confidence_threshold_p0": float(
                        getattr(runtime_settings, "issue_confidence_threshold_p0", 0.95) or 0.95
                    ),
                    "issue_confidence_threshold_p1": float(
                        getattr(runtime_settings, "issue_confidence_threshold_p1", 0.85) or 0.85
                    ),
                    "issue_confidence_threshold_p2": float(
                        getattr(runtime_settings, "issue_confidence_threshold_p2", 0.8) or 0.8
                    ),
                    "issue_confidence_threshold_p3": float(
                        getattr(runtime_settings, "issue_confidence_threshold_p3", 0.7) or 0.7
                    ),
                    "suppress_low_risk_hint_issues": bool(
                        getattr(runtime_settings, "suppress_low_risk_hint_issues", True)
                    ),
                    "hint_issue_confidence_threshold": float(
                        getattr(runtime_settings, "hint_issue_confidence_threshold", 0.85) or 0.85
                    ),
                    "hint_issue_evidence_cap": max(
                        0,
                        int(getattr(runtime_settings, "hint_issue_evidence_cap", 2) or 2),
                    ),
                },
                "findings": finding_payloads,
            }
        )

        issues = [
            DebateIssue(
                review_id=review_id,
                issue_id=str(item.get("issue_id") or f"iss_{uuid4().hex[:12]}"),
                title=str(item.get("title") or "待裁决议题"),
                summary=str(item.get("summary") or ""),
                finding_type=str(item.get("finding_type") or "risk_hypothesis"),
                file_path=str(item.get("file_path") or ""),
                line_start=int(item.get("line_start") or 1),
                status=str(item.get("status") or "open"),
                severity=str(item.get("severity") or "medium"),
                confidence=float(item.get("confidence") or 0.72),
                confidence_breakdown=dict(item.get("confidence_breakdown") or {}),
                finding_ids=[str(value) for value in item.get("finding_ids", [])],
                participant_expert_ids=[str(value) for value in item.get("participant_expert_ids", [])],
                evidence=[str(value) for value in item.get("evidence", [])],
                cross_file_evidence=[str(value) for value in item.get("cross_file_evidence", [])],
                assumptions=[str(value) for value in item.get("assumptions", [])],
                context_files=[str(value) for value in item.get("context_files", [])],
                direct_evidence=bool(item.get("direct_evidence")),
                needs_human=bool(item.get("needs_human")),
                verified=bool(item.get("verified")),
                needs_debate=bool(item.get("needs_debate")),
                verifier_name=str(item.get("verifier_name") or ""),
                tool_name=str(item.get("tool_name") or ""),
                tool_verified=bool(item.get("tool_verified")),
                resolution=str(item.get("resolution") or ""),
            )
            for item in graph_result.get("issues", [])
        ]
        issue_filter_decisions = [
            item
            for item in list(graph_result.get("issue_filter_decisions", []))
            if isinstance(item, dict)
        ]
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
            review.status = "waiting_human"
            review.phase = "human_gate"
            review.human_review_status = "requested"
            review.pending_human_issue_ids = pending_human_issue_ids
            review.completed_at = None
            review.duration_seconds = None
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
            review.duration_seconds = max(
                0.0,
                round((review.completed_at - (review.started_at or review.created_at)).total_seconds(), 3),
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
        self._abort_if_closed(review_id)
        final_summary, final_llm = self.main_agent_service.build_final_summary(
            review,
            issues,
            effective_runtime_settings,
            partial_failure_count=len(expert_failures),
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
        repository_context = dict(job.get("repository_context") or {})
        target_hunk = dict(job.get("target_hunk") or {})
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
                ],
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
            finding_type="risk_hypothesis",
            severity="medium",
            confidence=confidence,
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
            verification_needed=True,
            verification_plan="建议先重试失败专家；若仍失败，人工复核目标 hunk、关联源码和命中规则后再决定是否升级为 issue。",
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
            suggested_code=self._build_suggested_code(review.subject, file_path, line_start, expert.expert_id),
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
        if existing_jobs or not skipped_experts or not review.subject.changed_files:
            return None
        fallback_expert = next((item for item in enabled_experts if item.expert_id == FALLBACK_EXPERT_ID), None)
        if fallback_expert is None or not fallback_expert.enabled:
            return None
        fallback_file = review.subject.changed_files[0]
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

    def _release_expert_job_payload(self, job: dict[str, object]) -> None:
        """专家任务完成后尽快丢弃大对象，降低批次执行期间的峰值内存。"""

        for key in (
            "bound_documents",
            "knowledge_context",
            "rule_screening",
            "repository_context",
            "target_hunk",
            "related_files",
            "business_changed_files",
            "expected_checks",
            "disallowed_inference",
        ):
            job.pop(key, None)
        if sys.platform == "win32":
            gc.collect()

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

    def _run_expert_from_command(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        command_message: ConversationMessage,
        file_path: str,
        line_start: int,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
        llm_request_options: dict[str, int | float],
        bound_documents: list[object],
        knowledge_context: dict[str, object],
        rule_screening: dict[str, object],
        finding_payloads: list[dict[str, object]],
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
            related_files=list(job.get("related_files") or []),
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
            dict(job.get("repository_context") or {}),
            runtime_tool_results,
        )
        repository_context["routing_reason"] = job.get("routing_reason", "")
        repository_context["routing_confidence"] = job.get("routing_confidence", 0.0)
        target_hunk = dict(job.get("target_hunk") or {})
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
                    "related_files": list(job.get("related_files") or []),
                    "business_changed_files": list(job.get("business_changed_files") or []),
                    "target_hunk": target_hunk,
                    "repository_context": self._build_repository_context_metadata(repository_context),
                    "expected_checks": list(job.get("expected_checks") or []),
                    "disallowed_inference": list(job.get("disallowed_inference") or []),
                    "runtime_tool_results": self._build_runtime_tool_results_metadata(runtime_tool_results),
                    "design_doc_titles": self._normalize_text_list(
                        [item.get("title") for item in design_docs],
                        [],
                    ),
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
        for tool_result in runtime_tool_results:
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
                        "tool_category": "runtime",
                        "target_hunk": target_hunk,
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_tool_invoked",
                    phase="expert_review",
                    message=f"{expert.name_zh} 调用了运行时工具 {tool_name}",
                    payload={"expert_id": expert.expert_id, "tool_name": tool_name, "tool_category": "runtime"},
                )
            )
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

        severity, confidence = self._score_finding(review.subject, expert.expert_id)
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
        llm_result = self.llm_chat_service.complete_text(
            system_prompt=self._build_expert_system_prompt(expert, bound_documents, active_skills, rule_screening),
            user_prompt=self._build_expert_prompt(
                review.subject,
                expert,
                file_path,
                line_start,
                tool_evidence,
                runtime_tool_results,
                repository_context,
                target_hunk,
                bound_documents,
                list(job.get("disallowed_inference") or []),
                list(job.get("expected_checks") or []),
                active_skills,
                rule_screening,
            ),
            resolution=self.llm_chat_service.resolve_expert(expert, runtime_settings),
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
            },
        )
        MemoryProbe.log(
            "expert.after_llm",
            review_id=review.review_id,
            expert_id=expert.expert_id,
            llm_mode=llm_result.mode,
            llm_error=llm_result.error,
        )
        self._abort_if_closed(review.review_id)
        parsed = self._parse_expert_analysis(
            llm_result.text,
            review.subject,
            expert,
            file_path,
            line_start,
        )
        parsed = self._stabilize_expert_analysis(
            parsed,
            expert.expert_id,
            file_path,
            line_start,
            target_hunk,
            repository_context=repository_context,
            input_completeness=input_completeness,
        )
        design_alignment = self._extract_design_alignment(runtime_tool_results)
        severity = self._normalize_severity(parsed.get("severity"), severity)
        confidence = self._normalize_confidence(parsed.get("confidence"), confidence)
        parsed_line_start = self._normalize_line_start(parsed.get("line_start"), line_start)
        finding = ReviewFinding(
            review_id=review.review_id,
            expert_id=expert.expert_id,
            title=str(parsed.get("title") or self._build_finding_title(expert)),
            summary=str(parsed.get("claim") or self._build_finding_summary(review.subject, expert.expert_id)),
            finding_type=str(parsed.get("finding_type") or "risk_hypothesis"),
            severity=severity,
            confidence=confidence,
            file_path=file_path,
            line_start=parsed_line_start,
            evidence=self._build_evidence(review.subject, expert, file_path, tool_evidence, parsed),
            cross_file_evidence=[str(item).strip() for item in parsed.get("cross_file_evidence", []) if str(item).strip()],
            assumptions=[str(item).strip() for item in parsed.get("assumptions", []) if str(item).strip()],
            context_files=self._merge_context_files(
                parsed.get("context_files", []),
                repository_context,
                runtime_tool_results,
            ),
            matched_rules=self._normalize_text_list(parsed.get("matched_rules"), []),
            violated_guidelines=self._normalize_text_list(parsed.get("violated_guidelines"), []),
            rule_based_reasoning=str(parsed.get("rule_based_reasoning") or "").strip(),
            verification_needed=bool(parsed.get("verification_needed", parsed.get("needs_verification", True))),
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
                or self._build_remediation_strategy(review.subject, expert.expert_id, file_path)
            ),
            remediation_suggestion=str(
                parsed.get("suggested_fix")
                or self._build_remediation_suggestion(review.subject, expert.expert_id, file_path)
            ),
            remediation_steps=self._normalize_text_list(
                parsed.get("change_steps"),
                self._build_remediation_steps(review.subject, expert.expert_id, file_path),
            ),
            code_excerpt=self._build_code_excerpt(
                review.subject,
                file_path,
                parsed_line_start,
                expert.expert_id,
            ),
            code_context=self._build_finding_code_context(
                review.subject,
                file_path,
                parsed_line_start,
                target_hunk,
                repository_context,
                expert=expert,
                bound_documents=bound_documents,
                rule_screening=rule_screening,
            ),
            suggested_code=str(
                parsed.get("suggested_code")
                or self._build_suggested_code(review.subject, file_path, parsed_line_start, expert.expert_id)
            ),
            suggested_code_language=self._infer_code_language(file_path),
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
            return
        self._abort_if_closed(review.review_id)
        self.finding_repo.save(review.review_id, finding)
        MemoryProbe.log(
            "expert.after_finding_save",
            review_id=review.review_id,
            expert_id=expert.expert_id,
            finding_id=finding.finding_id,
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id=finding.finding_id,
                expert_id=expert.expert_id,
                message_type="expert_analysis",
                content=llm_result.text.strip(),
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
                    "target_hunk": target_hunk,
                    "repository_context": self._build_repository_context_metadata(repository_context),
                    "bound_document_titles": [str(getattr(item, "title", "") or "") for item in bound_documents[:8]],
                    "bound_documents": self._build_bound_document_metadata(bound_documents),
                    "knowledge_context": self._build_knowledge_context_metadata(knowledge_context),
                    "rule_screening": self._build_rule_screening_metadata(rule_screening),
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
                    "input_completeness": finding.code_context.get("input_completeness", {}),
                    "review_inputs": finding.code_context.get("review_inputs", {}),
                    **self._llm_message_metadata(llm_result),
                },
            )
        )
        finding_payloads.append(finding.model_dump(mode="json"))
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="finding_created",
                phase="expert_review",
                message=f"{expert.name_zh} 生成审核发现",
                payload={"finding_id": finding.finding_id, "expert_id": expert.expert_id},
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
        debate_participants = issue.participant_expert_ids[:max_debate_rounds] or ["correctness_business", "architecture_design"]
        debate_participants = [item for item in debate_participants if item in experts_by_id] or list(experts_by_id)[:2]
        debate_participants = debate_participants[:max_debate_rounds]
        previous_expert_id = self.main_agent_service.agent_id
        issue_file_path = issue.file_path or self._pick_file_path(review.subject, debate_participants[0] if debate_participants else "correctness_business")
        issue_line_start = issue.line_start or self.diff_excerpt_service.find_nearest_line(
            review.subject.unified_diff,
            issue_file_path,
            1,
        ) or 1
        if issue.needs_debate and debate_participants:
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
                    system_prompt=self._build_expert_system_prompt(expert, bound_documents, []),
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

    def _llm_message_metadata(self, llm_result) -> dict[str, object]:
        return {
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
        if expert_id == "ddd_specification":
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
        if expert_id == "ddd_specification":
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
            preferred_line = 42 if "service" in file_blob or "runtime" in file_blob else 24
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
        if expert_id == "ddd_specification":
            return f"领域建模相关改动涉及 {file_blob}，当前实现可能混淆领域规则、应用编排和基础设施职责，偏离 DDD 分层。"
        if expert_id == "test_verification":
            return f"当前改动涉及 {file_blob}，缺少与改动风险相匹配的回归测试或更强断言保护。"
        if expert_id == "architecture_design":
            return f"当前改动涉及 {file_blob}，模块边界、依赖方向或抽象层级出现了收缩，后续扩展成本会被放大。"
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
        if expert.expert_id == "ddd_specification":
            return "领域边界与职责分层偏离 DDD 规范"
        if expert.expert_id == "test_verification":
            return "缺少与改动匹配的验证保护"
        if expert.expert_id == "architecture_design":
            return "模块边界与抽象层次被削弱"
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
        if expert_id == "ddd_specification":
            return f"在 {file_path} 把领域规则、应用服务编排和基础设施访问重新分层，避免聚合职责外溢。"
        if expert_id == "test_verification":
            return f"围绕 {file_path} 的关键分支补充回归测试，并为异常路径增加断言。"
        if expert_id == "architecture_design":
            return f"把 {file_path} 中的规则判断与执行流程解耦，收敛依赖方向，避免跨层直接耦合。"
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
        if expert_id == "ddd_specification":
            return f"把 {file_path} 的领域规则、应用编排和基础设施访问重新拆层，先收回职责边界。"
        if expert_id == "test_verification":
            return f"先补测试锁住 {file_path} 当前风险，再根据断言结果决定是否继续改实现。"
        if expert_id == "architecture_design":
            return f"把 {file_path} 的流程控制和业务规则拆开，保留单一入口，避免跨层耦合继续扩散。"
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
        repository_excerpt = self._load_repository_source_excerpt(subject, file_path, line_start)
        if repository_excerpt:
            return repository_excerpt
        excerpt = self.diff_excerpt_service.extract_excerpt(subject.unified_diff, file_path, line_start)
        if excerpt:
            return excerpt
        return self._build_fallback_code_excerpt(file_path, line_start, expert_id)

    def _load_repository_source_excerpt(
        self,
        subject: ReviewSubject | dict[str, object],
        file_path: str,
        line_start: int,
        radius: int = 8,
    ) -> str:
        runtime = self.runtime_settings_service.get()
        service = RepositoryContextService.from_review_context(
            clone_url=runtime.code_repo_clone_url,
            local_path=runtime.code_repo_local_path,
            default_branch=runtime.code_repo_default_branch or runtime.default_target_branch,
            access_token=runtime.code_repo_access_token,
            auto_sync=runtime.code_repo_auto_sync,
            subject=subject,
        )
        if not service.is_ready():
            return ""
        context = service.load_file_context(file_path, max(1, line_start), radius=radius)
        return str(context.get("snippet") or "").strip()

    def _build_target_file_full_diff(self, subject: ReviewSubject, file_path: str) -> str:
        full_diff = self.diff_excerpt_service.extract_file_diff(subject.unified_diff, file_path)
        if not full_diff:
            return f"未从完整 diff 中提取到 {file_path} 的文件级变更，请结合目标 hunk 和代码仓上下文谨慎判断。"
        lines = full_diff.splitlines()
        if len(lines) <= 160:
            return full_diff
        return "\n".join(lines[:160]) + "\n... [目标文件完整 diff 过长，已截断展示前 160 行]"

    def _build_related_diff_summary(self, subject: ReviewSubject, target_file_path: str) -> str:
        related_paths = [
            str(path).strip()
            for path in list(subject.changed_files or [])
            if str(path).strip() and str(path).strip() != target_file_path
        ]
        if not related_paths:
            return "除目标文件外无其他变更文件。"
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
        return "\n\n".join(sections)

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
        input_completeness = self._build_review_input_completeness(
            subject,
            file_path,
            line_start,
            repository_context,
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
            "context_files": [
                str(item).strip()
                for item in list(repository_context.get("context_files") or [])[:10]
                if str(item).strip()
            ],
            "routing_reason": str(repository_context.get("routing_reason") or "").strip(),
            "input_completeness": input_completeness,
            "review_inputs": self._build_review_input_trace(
                expert=expert,
                bound_documents=bound_documents or [],
                rule_screening=rule_screening or {},
                repository_context=repository_context,
                language=language,
            ),
        }

    def _load_repository_problem_context(
        self,
        subject: ReviewSubject | dict[str, object],
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object],
    ) -> dict[str, object]:
        runtime = self.runtime_settings_service.get()
        service = RepositoryContextService.from_review_context(
            clone_url=runtime.code_repo_clone_url,
            local_path=runtime.code_repo_local_path,
            default_branch=runtime.code_repo_default_branch or runtime.default_target_branch,
            access_token=runtime.code_repo_access_token,
            auto_sync=runtime.code_repo_auto_sync,
            subject=subject,
        )
        if not service.is_ready():
            return {}
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if changed_lines:
            start_line = min(changed_lines)
            end_line = max(changed_lines)
        else:
            start_line = self._normalize_optional_line_value(target_hunk.get("start_line")) or line_start
            end_line = self._normalize_optional_line_value(target_hunk.get("end_line")) or start_line
        padding = self._compute_problem_context_padding(start_line, end_line, changed_lines)
        context = service.load_file_range(
            file_path,
            start_line,
            end_line,
            padding=padding,
            expand_to_block=True,
        )
        return dict(context) if isinstance(context, dict) else {}

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
        lines = [
            f"{line_start:>4} | def review_guard(payload):",
            f"{line_start + 1:>4} |     if payload.get('enabled'):",
            f"{line_start + 2:>4} |         return True",
            f"{line_start + 3:>4} |     return False",
        ]
        if expert_id == "security_compliance":
            lines = [
                f"{line_start:>4} | def review_guard(payload, user):",
                f"{line_start + 1:>4} |     if payload.get('enabled'):",
                f"{line_start + 2:>4} |         return True",
                f"{line_start + 3:>4} |     return False  # missing permission check",
            ]
        if expert_id == "architecture_design":
            lines = [
                f"{line_start:>4} | def review_guard(payload):",
                f"{line_start + 1:>4} |     service = RuntimeService()",
                f"{line_start + 2:>4} |     service.repo.save(payload)",
                f"{line_start + 3:>4} |     return service.policy.allow(payload)",
            ]
        if expert_id == "performance_reliability":
            lines = [
                f"{line_start:>4} | def load_reviews(db):",
                f"{line_start + 1:>4} |     rows = db.query('select * from reviews')",
                f"{line_start + 2:>4} |     return [hydrate(row) for row in rows]",
                f"{line_start + 3:>4} |     # missing timeout / batching / rollback handling",
            ]
        if expert_id == "test_verification":
            lines = [
                f"{line_start:>4} | def review_guard(payload):",
                f"{line_start + 1:>4} |     if payload.get('enabled'):",
                f"{line_start + 2:>4} |         return True",
                f"{line_start + 3:>4} |     return False  # no regression test covers this branch",
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
                    "function shouldEnableReview(payload: ReviewPayload): boolean {\n"
                    "  return Boolean(payload.enabled);\n"
                    "}\n\n"
                    "export function reviewGuard(payload: ReviewPayload, deps: { policy: ReviewPolicy }): boolean {\n"
                    "  if (!shouldEnableReview(payload)) {\n"
                    "    return false;\n"
                    "  }\n\n"
                    "  return deps.policy.allow(payload);\n"
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
                    "def should_enable_review(payload: dict) -> bool:\n"
                    "    return bool(payload.get(\"enabled\"))\n\n"
                    "def review_guard(payload: dict, policy: ReviewPolicy) -> bool:\n"
                    "    if not should_enable_review(payload):\n"
                    "        return False\n"
                    "    return policy.allow(payload)\n"
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
                "- 遵循 Java / Spring 通用代码规范：命名清晰，单个方法职责收敛，避免把校验、事务、持久化、远程调用混成一个长方法。\n"
                "- 关注输入校验、空值处理、异常边界、日志脱敏、权限/租户隔离，以及 @Transactional 范围内的副作用。\n"
                "- 检查 Repository / JPA / MyBatis 查询是否存在无分页、全表扫描、N+1、批量逐条写、EAGER/级联加载风险。\n"
                "- 若结论依赖调用链、ORM 映射或事务传播，必须结合已提供源码上下文和工具证据，证据不足时只能输出 risk_hypothesis。"
            )
        if normalized in {"javascript", "jsx", "typescript", "tsx"}:
            return (
                "- 遵循 JavaScript / TypeScript 通用代码规范：命名清晰，避免隐藏副作用，保持函数职责单一，异步流程要显式处理错误和资源释放。\n"
                "- 关注输入校验、鉴权边界、日志与敏感信息暴露、空值/undefined 处理、Promise/await 错误传播和并发竞态。\n"
                "- 检查数据库/HTTP/缓存调用是否存在无边界重试、批量串行、未分页查询、未取消请求或阻塞主路径的问题。\n"
                "- 若结论依赖运行时分支、类型收窄或框架约定，必须引用已提供代码证据；证据不足时只能输出 risk_hypothesis。"
            )
        return ""

    def _build_expert_prompt(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        tool_evidence: list[dict[str, object]],
        runtime_tool_results: list[dict[str, object]],
        repository_context: dict[str, object],
        target_hunk: dict[str, object],
        bound_documents: list[object],
        disallowed_inference: list[str],
        expected_checks: list[str],
        active_skills: list[object],
        rule_screening: dict[str, object] | None = None,
    ) -> str:
        """构造专家最终输入给 LLM 的用户提示词。

        这里强制把 diff、代码仓上下文、运行时工具结果、规范文档和禁止推断规则合并，
        目的是把专家的审查边界和证据来源约束得足够明确。
        """
        capability_summary = self.capability_service.build_capability_summary(expert, tool_evidence)
        code_excerpt = self._build_code_excerpt(subject, file_path, line_start, expert.expert_id)
        target_file_full_diff = self._build_target_file_full_diff(subject, file_path)
        related_diff_summary = self._build_related_diff_summary(subject, file_path)
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=target_file_full_diff,
        )
        prompt_repository_context = dict(repository_context)
        if list(java_quality.get("signals") or []):
            prompt_repository_context["java_quality_signals"] = list(java_quality.get("signals") or [])
        if str(java_quality.get("summary") or "").strip():
            prompt_repository_context["java_quality_signal_summary"] = str(java_quality.get("summary") or "").strip()
        runtime_tool_summary = self._build_runtime_tool_summary(runtime_tool_results)
        repository_context_summary = self._build_repository_context_summary(prompt_repository_context, runtime_tool_results)
        repository_source_blocks = self._build_repository_source_blocks(prompt_repository_context, runtime_tool_results)
        hunk_summary = self._build_hunk_summary(target_hunk)
        review_spec_summary = self._build_review_spec_summary(expert.review_spec)
        bound_documents_summary = self._build_bound_documents_summary(bound_documents)
        rule_screening_summary = self._build_rule_screening_summary(rule_screening or {})
        active_skill_summary = self._build_active_skill_summary(active_skills)
        design_doc_summary = self._build_design_doc_summary(subject)
        language = self._infer_code_language(file_path)
        language_general_guidance = self._build_language_general_guidance(language)
        java_ddd_focus = self._build_java_ddd_review_focus(language, expert.expert_id, prompt_repository_context)
        input_completeness_summary = self._build_review_input_completeness_summary(
            subject,
            file_path,
            line_start,
            prompt_repository_context,
            expert=expert,
            bound_documents=bound_documents,
            rule_screening=rule_screening or {},
            language=language,
        )
        has_design_docs = bool(self._review_design_docs(subject))
        business_changed_files = self._business_changed_files(subject)
        routing_reason = str(repository_context.get("routing_reason") or "").strip()
        design_contract = (
            '"design_alignment_status":"aligned|partially_aligned|misaligned",'
            '"matched_design_points":["已经实现的设计点"],'
            '"missing_design_points":["缺失的设计点"],'
            '"extra_implementation_points":["超出设计的实现"],'
            '"design_conflicts":["与设计冲突的实现"],'
            if has_design_docs
            else ""
        )
        design_instruction = (
            "本次已绑定详细设计文档，你需要严格核对实现与设计是否一致，并在 JSON 中输出设计一致性字段。\n"
            if has_design_docs
            else "本次未绑定详细设计文档，不要执行设计一致性检查，也不要输出任何 design_* / 设计一致性字段。\n"
        )
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"专家: {expert.expert_id} / {expert.name_zh}\n"
            f"角色: {expert.role}\n"
            f"目标文件: {file_path}\n"
            f"目标行号: {line_start}\n"
            f"业务变更文件: {', '.join(business_changed_files) or '未提供'}\n"
            f"主Agent派工理由: {routing_reason or '未提供'}\n"
            f"能力约束:\n{capability_summary}\n"
            f"规范提要:\n{review_spec_summary}\n"
            f"已激活技能:\n{active_skill_summary}\n"
            f"已绑定参考文档:\n{bound_documents_summary}\n"
            f"规则遍历结果:\n{rule_screening_summary}\n"
            f"输入完整性校验:\n{input_completeness_summary}\n"
            f"语言通用规范提示:\n{language_general_guidance or '当前目标文件未命中已配置的语言通用规范提示，请仅依据专家规范、规则和代码证据审查。'}\n"
            f"本次审核绑定的详细设计文档:\n{design_doc_summary}\n"
            f"目标 hunk:\n{hunk_summary}\n"
            f"目标文件完整 diff:\n{target_file_full_diff}\n"
            f"其他变更文件摘要:\n{related_diff_summary}\n"
            f"运行时工具调用结果:\n{runtime_tool_summary}\n"
            f"代码仓上下文:\n{repository_context_summary}\n"
            f"关键源码上下文:\n{repository_source_blocks}\n"
            f"当前代码片段:\n{code_excerpt}\n"
            f"必查项: {' / '.join(expected_checks[:5]) or expert.role}\n"
            f"{java_ddd_focus}"
            f"禁止推断: {' / '.join(disallowed_inference[:5]) or '证据不足时只能输出待验证风险'}\n"
            f"你必须完整阅读并严格遵守系统提供的《审视规范文档》，再结合真实 diff、代码仓上下文和技能结果做审查。\n"
            f"请优先基于目标文件完整 diff 做审查，再结合其他变更文件摘要和代码仓上下文判断影响范围，避免泛泛而谈，不要评论未涉及的文件，不要越过你的职责边界。\n"
            f"{design_instruction}"
            f"如果你的结论依赖“当前 diff 没显示某段代码”“可能存在未注入/未调用/未校验”，"
            f"你必须把它标记为 risk_hypothesis，并写入 assumptions 和 verification_plan，不能输出 direct_defect。\n"
            f"你必须只输出一个 JSON 对象，不要输出 Markdown，不要输出额外解释。\n"
            f"JSON 字段要求:\n"
            f'{{"ack":"先回应主Agent派工","title":"一句话问题标题","finding_type":"direct_defect|risk_hypothesis|test_gap|design_concern","claim":"必须落在当前文件/行号的风险结论","severity":"blocker|high|medium|low","line_start":{line_start},"line_end":{line_start},"matched_rules":["命中的规范条款"],"violated_guidelines":["违反的具体规范"],"rule_based_reasoning":"说明为何违反规范以及规范如何约束当前改动","evidence":["至少2条具体代码证据"],"cross_file_evidence":["跨文件佐证"],"assumptions":["若有推断必须写明"],"context_files":["引用的目标分支文件"],{design_contract}"why_it_matters":"影响说明","fix_strategy":"一句话说明修改思路","suggested_fix":"详细说明应该怎么改","change_steps":["按顺序写清楚 2-4 个修改步骤"],"suggested_code":"给出建议修改后的完整代码片段","confidence":0.0,"verification_needed":true,"verification_plan":"需要如何继续验证"}}'
        )

    def _build_review_input_completeness_summary(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
        *,
        expert: ExpertProfile,
        bound_documents: list[object],
        rule_screening: dict[str, object],
        language: str,
    ) -> str:
        payload = self._build_review_input_completeness(
            subject,
            file_path,
            line_start,
            repository_context,
            expert=expert,
            bound_documents=bound_documents,
            rule_screening=rule_screening,
            language=language,
        )
        lines = [
            f"- 专家规范: {'已提供' if payload['review_spec_present'] else '缺失'}",
            f"- 语言通用规范提示: {'已提供' if payload['language_guidance_present'] else '缺失'}",
            f"- 绑定规则: {payload['matched_rule_count']} 条命中 / {payload['enabled_rule_count']} 条启用",
            f"- 绑定参考文档: {payload['bound_document_count']} 份",
            f"- 变更代码原文: {'已提供' if payload['target_file_diff_present'] else '缺失'}",
            f"- 当前源码上下文: {'已提供' if payload['source_context_present'] else '缺失'}",
            f"- 关联源码上下文: {payload['related_context_count']} 段",
        ]
        missing_sections = list(payload.get("missing_sections") or [])
        if missing_sections:
            lines.append(f"- 缺失项: {' / '.join(missing_sections[:6])}")
            lines.append("- 若结论依赖缺失项，只能输出 risk_hypothesis，并写清 verification_plan。")
        else:
            lines.append("- 当前未缺失关键输入，可基于规范、规则、变更代码和关联上下文直接审查。")
        return "\n".join(lines)

    def _build_review_input_completeness(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
        *,
        expert: ExpertProfile | None,
        bound_documents: list[object],
        rule_screening: dict[str, object],
        language: str,
    ) -> dict[str, object]:
        target_file_diff_present = bool(self._build_target_file_full_diff(subject, file_path).strip())
        language_guidance_present = bool(self._build_language_general_guidance(language).strip())
        source_context_present = bool(
            self._load_repository_problem_context(subject, file_path, line_start, {}).get("snippet")
            or self._load_repository_source_excerpt(subject, file_path, line_start).strip()
            or str((repository_context.get("primary_context") or {}).get("snippet") or "").strip()
        )
        related_context_count = sum(
            1
            for collection in [
                list(repository_context.get("related_source_snippets") or []),
                list(repository_context.get("related_contexts") or []),
                list(repository_context.get("caller_contexts") or []),
                list(repository_context.get("callee_contexts") or []),
                list(repository_context.get("domain_model_contexts") or []),
                list(repository_context.get("persistence_contexts") or []),
            ]
            for item in collection[:6]
            if isinstance(item, dict) and str(item.get("snippet") or "").strip()
        )
        enabled_rule_count = int(rule_screening.get("enabled_rules") or 0)
        matched_rule_count = len(list(rule_screening.get("matched_rules_for_llm") or []))
        review_spec_present = bool(str(getattr(expert, "review_spec", "") or "").strip())
        bound_document_count = len([item for item in bound_documents if item is not None])
        missing_sections: list[str] = []
        if not review_spec_present:
            missing_sections.append("专家规范")
        if not language_guidance_present:
            missing_sections.append("语言通用规范提示")
        if enabled_rule_count <= 0:
            missing_sections.append("绑定规则")
        if not target_file_diff_present:
            missing_sections.append("变更代码原文")
        if not source_context_present:
            missing_sections.append("当前源码上下文")
        if related_context_count <= 0:
            missing_sections.append("关联源码上下文")
        return {
            "review_spec_present": review_spec_present,
            "language_guidance_present": language_guidance_present,
            "enabled_rule_count": enabled_rule_count,
            "matched_rule_count": matched_rule_count,
            "bound_document_count": bound_document_count,
            "target_file_diff_present": target_file_diff_present,
            "source_context_present": source_context_present,
            "related_context_count": related_context_count,
            "missing_sections": missing_sections,
        }

    def _build_review_input_trace(
        self,
        *,
        expert: ExpertProfile | None,
        bound_documents: list[object],
        rule_screening: dict[str, object],
        repository_context: dict[str, object],
        language: str,
    ) -> dict[str, object]:
        matched_rules = []
        for item in list(rule_screening.get("matched_rules_for_llm") or [])[:8]:
            if not isinstance(item, dict):
                continue
            matched_rules.append(
                {
                    "rule_id": str(item.get("rule_id") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "priority": str(item.get("priority") or "").strip(),
                }
            )
        bound_doc_titles = []
        for item in bound_documents[:6]:
            title = str(getattr(item, "title", "") or "").strip()
            if title:
                bound_doc_titles.append(title)
        return {
            "expert_id": str(getattr(expert, "expert_id", "") or "").strip(),
            "review_spec_present": bool(str(getattr(expert, "review_spec", "") or "").strip()),
            "language_guidance_language": str(language or "").strip(),
            "language_guidance_present": bool(self._build_language_general_guidance(language).strip()),
            "language_guidance_topics": self._build_language_guidance_topics(language),
            "bound_document_titles": bound_doc_titles,
            "matched_rules": matched_rules,
            "context_files": [
                str(item).strip()
                for item in list(repository_context.get("context_files") or [])[:10]
                if str(item).strip()
            ],
        }

    def _build_language_guidance_topics(self, language: str) -> list[str]:
        normalized = str(language or "").strip().lower()
        if normalized == "java":
            return ["命名与职责", "输入校验与安全边界", "事务与副作用", "Repository/ORM 查询风险"]
        if normalized in {"javascript", "jsx", "typescript", "tsx"}:
            return ["命名与职责", "输入校验与鉴权边界", "异步错误处理", "数据访问与性能边界"]
        return []

    def _build_java_ddd_review_focus(
        self,
        language: str,
        expert_id: str,
        repository_context: dict[str, object],
    ) -> str:
        if language != "java":
            return ""
        java_review_mode = str(repository_context.get("java_review_mode") or "").strip() or "general"
        java_context_signals = [
            str(item).strip()
            for item in list(repository_context.get("java_context_signals") or [])
            if str(item).strip()
        ]
        java_quality_signals = [
            str(item).strip()
            for item in list(repository_context.get("java_quality_signals") or [])
            if str(item).strip()
        ]
        available_sections: list[str] = []
        for key, label in [
            ("current_class_context", "当前类问题片段"),
            ("parent_contract_contexts", "父接口/抽象类"),
            ("caller_contexts", "调用方 Controller/ApplicationService"),
            ("callee_contexts", "被调方 Repository/DomainService"),
            ("domain_model_contexts", "Aggregate/Entity/ValueObject/DomainEvent"),
            ("transaction_context", "事务边界与调用链"),
            ("persistence_contexts", "ORM/SQL/Mapper"),
        ]:
            value = repository_context.get(key)
            if isinstance(value, dict) and value:
                available_sections.append(label)
            if isinstance(value, list) and value:
                available_sections.append(label)
        base = [
            "Java 通用审查要求：",
            f"- 当前模式: {'Java DDD 增强模式' if java_review_mode == 'ddd_enhanced' else 'Java 通用模式'}",
            f"- 可用上下文: {' / '.join(available_sections) or '仅有基础 diff 与代码片段'}",
            f"- 已识别信号: {' / '.join(java_context_signals[:8]) or '未识别到额外 Java 结构信号'}",
            f"- 已识别通用质量信号: {' / '.join(java_quality_signals[:8]) or '未识别到额外 Java 通用质量信号'}",
            "- 不要只基于单个 diff hunk 下结论，必须结合当前类、调用方、被调方、事务边界和持久化层判断。",
        ]
        if expert_id == "security_compliance":
            base.extend(
                [
                    "- 重点检查 Controller/ApplicationService 入口是否完成参数校验、权限校验、租户隔离和敏感字段脱敏。",
                    "- 重点检查 Repository/SQL/Mapper 是否存在拼接查询、越权查询、批量更新越边界、日志泄漏敏感信息。",
                    "- 若结论依赖未展示的鉴权实现，只能输出 risk_hypothesis，并明确 verification_plan。",
                ]
            )
        elif expert_id == "performance_reliability":
            base.extend(
                [
                    "- 重点检查事务边界内是否包含远程调用、消息发送、循环写库、多仓储写入和大对象装载。",
                    "- 重点检查 ORM/Mapper 是否引入 N+1、全表扫描、隐式 EAGER 加载、批量场景逐条写入。",
                    "- 必须判断当前聚合/仓储调用链是否会放大锁竞争、超时重试或资源占用。",
                ]
            )
        elif expert_id == "architecture_design":
            base.extend(
                [
                    "- 重点检查 Controller/ApplicationService/DomainService/Repository 是否越层依赖、边界绕过、基础设施泄漏。",
                    "- 重点检查 Service 是否承担过多编排与规则逻辑，是否把持久化和流程控制揉在一起。",
                    "- 必须判断事务边界和模块边界是否一致；若处于 DDD 增强模式，再额外判断聚合边界是否被破坏。",
                ]
            )
        elif expert_id == "ddd_specification":
            base.extend(
                [
                    "- 重点检查聚合根是否真正维护不变量，ValueObject 是否保持不可变语义。",
                    "- 重点检查 DomainEvent 是否在正确边界发布，Repository 是否只服务聚合根而不是应用层拼装。",
                    "- 必须说明当前改动是落在领域层、应用层还是基础设施层，以及是否破坏 DDD 分层职责。",
                ]
            )
        else:
            base.append("- 若上下文中存在 Java 结构化上下文，请优先引用这些结构化上下文，而不是只看单行改动。")
        if java_review_mode == "ddd_enhanced":
            base.extend(
                [
                    "- Java DDD 增强要求: 当前变更命中了领域建模信号，请额外检查聚合边界、领域事件和值对象语义。",
                    "- 若结论涉及领域层责任划分，必须明确说明改动落在领域层、应用层还是基础设施层。",
                ]
            )
        return "\n".join(base) + "\n"

    def _build_expert_system_prompt(
        self,
        expert: ExpertProfile,
        bound_documents: list[object],
        active_skills: list[object] | None = None,
        rule_screening: dict[str, object] | None = None,
    ) -> str:
        base_prompt = expert.system_prompt or f"你是{expert.name_zh}，你的职责是{expert.role}。"
        bound_documents_text = self._build_bound_documents_fulltext(bound_documents)
        active_skill_text = self._build_active_skill_fulltext(active_skills or [])
        rule_screening_text = self._build_rule_screening_fulltext(rule_screening or {})
        return (
            f"{base_prompt}\n\n"
            f"《审视规范文档》开始\n"
            f"{expert.review_spec or '未提供额外规范文档，请至少遵守专家职责与证据优先原则。'}\n"
            f"《审视规范文档》结束\n\n"
            f"{active_skill_text}\n\n"
            f"{bound_documents_text}\n\n"
            f"{rule_screening_text}\n\n"
            f"执行纪律：\n"
            f"1. 只在你的职责边界内下结论。\n"
            f"2. 结论必须绑定具体文件和代码行，禁止泛化空谈。\n"
            f"3. 没有代码证据时，只能提出“需要验证”，不能伪造确定性结论。\n"
            f"4. 修复建议必须可执行，不能只写“建议优化”。\n"
            f"5. 必须讲清楚怎么改，并给出建议修改后的完整代码片段。\n"
            f"6. 必须显式引用命中的规范条款和违反的规范要求。\n"
            f"7. 输出必须遵守 JSON contract。"
        )

    def _build_active_skill_summary(self, active_skills: list[object]) -> str:
        if not active_skills:
            return "本轮未激活额外 skill。"
        lines: list[str] = []
        for skill in active_skills:
            skill_id = str(getattr(skill, "skill_id", "") or "").strip()
            description = str(getattr(skill, "description", "") or "").strip()
            required_tools = [str(item).strip() for item in list(getattr(skill, "required_tools", []) or []) if str(item).strip()]
            lines.append(f"- {skill_id}: {description or '无描述'}")
            if required_tools:
                lines.append(f"  * tools: {' / '.join(required_tools[:6])}")
        return "\n".join(lines)

    def _build_active_skill_fulltext(self, active_skills: list[object]) -> str:
        if not active_skills:
            return "《已激活 Skills》开始\n本轮未激活额外 skill。\n《已激活 Skills》结束"
        sections = ["《已激活 Skills》开始"]
        for index, skill in enumerate(active_skills, start=1):
            skill_id = str(getattr(skill, "skill_id", "") or "").strip() or f"skill-{index}"
            name = str(getattr(skill, "name", "") or "").strip() or skill_id
            sections.append(f"## Skill {index}: {name} ({skill_id})")
            sections.append(str(getattr(skill, "prompt_body", "") or "").strip() or "无额外 skill 正文。")
        sections.append("《已激活 Skills》结束")
        return "\n".join(sections)

    def _collect_skill_tools(self, active_skills: list[object]) -> list[str]:
        tool_names: list[str] = []
        for skill in active_skills:
            for item in list(getattr(skill, "required_tools", []) or []):
                tool_name = str(item).strip()
                if tool_name and tool_name not in tool_names:
                    tool_names.append(tool_name)
        return tool_names

    def _review_design_docs(self, subject: ReviewSubject) -> list[dict[str, object]]:
        value = subject.metadata.get("design_docs", [])
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _build_design_doc_summary(self, subject: ReviewSubject) -> str:
        """把本次审核绑定的详细设计文档压缩成适合给专家阅读的摘要。"""
        design_docs = self._review_design_docs(subject)
        if not design_docs:
            return "本次审核未绑定详细设计文档。"
        lines: list[str] = []
        for index, item in enumerate(design_docs[:4], start=1):
            title = str(item.get("title") or item.get("filename") or f"设计文档 {index}").strip()
            filename = str(item.get("filename") or "").strip()
            content = str(item.get("content") or "").strip()
            line = f"- {title}"
            if filename:
                line += f" · {filename}"
            lines.append(line)
            if content:
                excerpt_lines = [text.strip() for text in content.splitlines() if text.strip()]
                if excerpt_lines:
                    lines.append(f"  * 摘要: {' '.join(excerpt_lines[:3])[:220]}")
        return "\n".join(lines)

    def _parse_expert_analysis(
        self,
        text: str,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
    ) -> dict[str, object]:
        parsed = self._parse_json_object(text)
        if parsed:
            return parsed
        has_design_docs = bool(self._review_design_docs(subject))
        return {
            "ack": self._extract_structured_field(text, "回应主Agent"),
            "title": self._extract_structured_field(text, "问题标题") or self._build_finding_title(expert),
            "claim": self._extract_structured_field(text, "风险结论")
            or self._build_finding_summary(subject, expert.expert_id),
            "finding_type": "risk_hypothesis",
            "severity": "",
            "line_start": line_start,
            "line_end": line_start,
            "matched_rules": [],
            "violated_guidelines": [],
            "rule_based_reasoning": self._extract_structured_field(text, "规范依据"),
            "evidence": [self._extract_structured_field(text, "代码证据")] if self._extract_structured_field(text, "代码证据") else [],
            "cross_file_evidence": [],
            "assumptions": [],
            "context_files": [],
            "why_it_matters": self._extract_structured_field(text, "证据诉求"),
            "fix_strategy": self._extract_structured_field(text, "修改思路")
            or self._build_remediation_strategy(subject, expert.expert_id, file_path),
            "suggested_fix": self._extract_structured_field(text, "修复建议")
            or self._build_remediation_suggestion(subject, expert.expert_id, file_path),
            "change_steps": self._build_remediation_steps(subject, expert.expert_id, file_path),
            "suggested_code": self._build_suggested_code(subject, file_path, line_start, expert.expert_id),
            "confidence": 0.0,
            "verification_needed": True,
            "verification_plan": "需要补充关联上下文、调用链和测试证据。",
            "design_alignment_status": "insufficient_design_context" if has_design_docs else "",
            "matched_design_points": [],
            "missing_design_points": [],
            "extra_implementation_points": [],
            "design_conflicts": [],
        }

    def _stabilize_expert_analysis(
        self,
        parsed: dict[str, object],
        expert_id: str,
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object],
        repository_context: dict[str, object] | None = None,
        input_completeness: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """对专家输出做二次收敛，压制明显误报。"""
        result = dict(parsed)
        text_blob = "\n".join(
            [
                str(result.get("title") or ""),
                str(result.get("claim") or ""),
                *[str(item) for item in list(result.get("evidence") or [])],
                *[str(item) for item in list(result.get("assumptions") or [])],
            ]
        )
        excerpt = str(target_hunk.get("excerpt") or "")
        normalized_excerpt_lines = []
        for line in excerpt.splitlines():
            cleaned = line
            if "|" in cleaned:
                cleaned = cleaned.split("|", 1)[1]
            cleaned = cleaned.strip()
            normalized_excerpt_lines.append(cleaned)
        import_only_excerpt = bool(excerpt) and all(
            line.startswith(("+import", "-import", "import ")) or not line
            for line in normalized_excerpt_lines
        )
        speculative_tokens = ["未显示", "未看到", "可能", "若", "如果", "假设", "推测"]
        import_inference_tokens = ["constructor", "注入", "依赖缺失", "未注入", "Cannot resolve dependency"]
        has_speculative_language = any(token in text_blob for token in speculative_tokens)
        has_import_inference = any(token in text_blob for token in import_inference_tokens)
        if has_speculative_language:
            result["finding_type"] = "risk_hypothesis"
            result["verification_needed"] = True
            result["direct_evidence"] = False
            result["confidence"] = min(float(result.get("confidence") or 0.0), 0.4)
            if str(result.get("severity") or "").lower() in {"blocker", "critical", "high"}:
                result["severity"] = "medium"
            assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
            assumption = "当前结论依赖 diff 片段外信息或未展示的实现细节，需要查看完整方法/类定义后再确认。"
            if assumption not in assumptions:
                assumptions.append(assumption)
            result["assumptions"] = assumptions
            result["verification_plan"] = (
                str(result.get("verification_plan") or "").strip()
                or "需要回看完整 diff、相关方法实现和调用链，确认推断是否成立。"
            )
        if import_only_excerpt and has_import_inference:
            result["verification_plan"] = (
                str(result.get("verification_plan") or "").strip()
                or "需要检查完整类定义和 constructor 注入，不能仅凭 import 变化下结论。"
            )
            assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
            assumption = "当前结论基于 import 变化推断，尚未看到完整类定义与 constructor。"
            if assumption not in assumptions:
                assumptions.append(assumption)
            result["assumptions"] = assumptions
            result["confidence"] = min(float(result.get("confidence") or 0.0), 0.45)
            if str(result.get("severity") or "").lower() in {"blocker", "critical", "high"}:
                result["severity"] = "medium"
        if expert_id == "performance_reliability":
            perf_tokens = [
                "超时",
                "重试",
                "限流",
                "队列",
                "吞吐",
                "性能",
                "缓存",
                "序列化",
                "响应体",
                "锁",
                "热点",
                "退化",
                "并发",
                "cpu",
                "内存",
                "network",
                "latency",
                "throughput",
                "cache",
                "timeout",
                "retry",
            ]
            has_perf_signal = any(token.lower() in text_blob.lower() for token in perf_tokens)
            if not has_perf_signal:
                result["finding_type"] = "design_concern"
                result["severity"] = "low"
                result["confidence"] = min(float(result.get("confidence") or 0.0), 0.35)
                result["verification_needed"] = True

        effective_line_start = self._stabilize_line_start(result.get("line_start"), line_start, target_hunk)
        result["line_start"] = effective_line_start
        result["line_end"] = self._stabilize_line_end(result.get("line_end"), effective_line_start, target_hunk)
        result["matched_rules"] = [str(item).strip() for item in list(result.get("matched_rules") or []) if str(item).strip()]
        result["violated_guidelines"] = [
            str(item).strip() for item in list(result.get("violated_guidelines") or []) if str(item).strip()
        ]
        result = self._enrich_java_domain_finding_language(result, expert_id)
        if not str(result.get("rule_based_reasoning") or "").strip():
            matched_rules = list(result.get("matched_rules") or [])
            violated = list(result.get("violated_guidelines") or [])
            if matched_rules or violated:
                result["rule_based_reasoning"] = (
                    f"命中规范: {' / '.join(matched_rules[:3]) or '无'}；"
                    f"违反规范: {' / '.join(violated[:3]) or '无'}。"
                )
        result["context_files"] = [str(item).strip() for item in list(result.get("context_files") or []) if str(item).strip()]
        result["evidence"] = [str(item).strip() for item in list(result.get("evidence") or []) if str(item).strip()]
        result["matched_design_points"] = self._normalize_text_list(result.get("matched_design_points"), [])
        result["missing_design_points"] = self._normalize_text_list(result.get("missing_design_points"), [])
        result["extra_implementation_points"] = self._normalize_text_list(result.get("extra_implementation_points"), [])
        result["design_conflicts"] = self._normalize_text_list(result.get("design_conflicts"), [])
        result["design_alignment_status"] = str(result.get("design_alignment_status") or "").strip()
        result = self._enrich_java_quality_signal_language(
            result,
            expert_id,
            file_path,
            target_hunk,
            repository_context or {},
        )
        result = self._apply_input_quality_gate(result, input_completeness or {})
        return result

    def _apply_input_quality_gate(
        self,
        parsed: dict[str, object],
        input_completeness: dict[str, object],
    ) -> dict[str, object]:
        result = dict(parsed)
        missing_sections = [
            str(item).strip()
            for item in list(input_completeness.get("missing_sections") or [])
            if str(item).strip()
        ]
        required_sections = {"专家规范", "语言通用规范提示", "变更代码原文", "当前源码上下文", "关联源码上下文"}
        missing_required = [item for item in missing_sections if item in required_sections]
        if not missing_required:
            return result

        result["finding_type"] = "risk_hypothesis"
        result["verification_needed"] = True
        result["direct_evidence"] = False
        result["confidence"] = min(float(result.get("confidence") or 0.0), 0.35 if len(missing_required) >= 2 else 0.4)
        if str(result.get("severity") or "").lower() in {"blocker", "critical", "high"}:
            result["severity"] = "medium"

        assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
        assumption = f"当前审查输入缺失: {' / '.join(missing_required[:5])}，结论已自动降级为待验证风险。"
        if assumption not in assumptions:
            assumptions.append(assumption)
        result["assumptions"] = assumptions

        existing_plan = str(result.get("verification_plan") or "").strip()
        gate_plan = f"先补齐 {' / '.join(missing_required[:5])}，再重新审查并确认当前风险是否成立。"
        result["verification_plan"] = existing_plan or gate_plan
        return result

    def _enrich_java_domain_finding_language(
        self,
        parsed: dict[str, object],
        expert_id: str,
    ) -> dict[str, object]:
        if expert_id not in {"ddd_specification", "architecture_design"}:
            return parsed

        matched_rules = [str(item).strip().upper() for item in list(parsed.get("matched_rules") or []) if str(item).strip()]
        if not any(rule.startswith("DDD-JDDD-001") or rule.startswith("ARCH-JDDD-002") for rule in matched_rules):
            return parsed

        result = dict(parsed)
        title = str(result.get("title") or "").strip()
        claim = str(result.get("claim") or "").strip()
        text_blob = f"{title}\n{claim}".lower()

        needs_course_create = "course.create" not in text_blob
        needs_aggregate = "aggregate" not in text_blob
        needs_factory = "factory" not in text_blob
        needs_domain_event = "domain event" not in text_blob

        if needs_aggregate or needs_factory:
            suffix = "Aggregate factory bypass"
            if suffix.lower() not in title.lower():
                title = f"{title} ({suffix})" if title else suffix
        result["title"] = title

        additions: list[str] = []
        if needs_course_create:
            additions.append("当前变更绕过了 Course.create")
        if needs_aggregate or needs_factory:
            additions.append("这属于 aggregate factory bypass")
        if needs_domain_event:
            additions.append("并可能让 domain event 录制/发布语义退化")
        if additions:
            claim = claim.rstrip("。")
            suffix = "；".join(additions)
            result["claim"] = f"{claim}；{suffix}。".strip("；")
        else:
            result["claim"] = claim
        return result

    def _enrich_java_quality_signal_language(
        self,
        parsed: dict[str, object],
        expert_id: str,
        file_path: str,
        target_hunk: dict[str, object],
        repository_context: dict[str, object],
    ) -> dict[str, object]:
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=str(target_hunk.get("excerpt") or ""),
        )
        signal_set = {
            str(item).strip()
            for item in list(java_quality.get("signals") or [])
            if str(item).strip()
        }
        if not signal_set:
            return parsed

        matched_terms = [
            str(item).strip()
            for item in list(java_quality.get("matched_terms") or [])
            if str(item).strip()
        ]
        signal_terms = {
            str(key).strip(): [str(item).strip() for item in list(value or []) if str(item).strip()]
            for key, value in dict(java_quality.get("signal_terms") or {}).items()
            if str(key).strip()
        }
        result = self._enrich_java_domain_finding_language(dict(parsed), expert_id)
        title = str(result.get("title") or "").strip()
        summary = str(result.get("summary") or "").strip()
        claim = str(result.get("claim") or "").strip()
        claim_blob = f"{title}\n{claim}".lower()
        evidence = [str(item).strip() for item in list(result.get("evidence") or []) if str(item).strip()]
        summary_parts = [summary] if summary else []

        if "query_semantics_weakened" in signal_set and expert_id in {
            "database_analysis",
            "correctness_business",
            "performance_reliability",
            "security_compliance",
        }:
            phrase = "当前变更把 equal 精确匹配放宽成 like/contains 模糊匹配"
            summary_phrase = "查询语义从精确匹配退化为模糊匹配，可能扩大结果范围并削弱索引命中。"
            evidence_phrase = "检测到查询语义从 equal 精确匹配退化为 like 模糊匹配。"
            if "查询语义" not in title:
                title = f"{title}（查询语义退化）" if title else "查询语义退化"
            if "like" not in claim_blob:
                claim = f"{claim.rstrip('。')}；{phrase}。".strip("；")
            if summary_phrase not in summary_parts:
                summary_parts.append(summary_phrase)
            if evidence_phrase not in evidence:
                evidence.append(evidence_phrase)

        if "naming_convention_violation" in signal_set:
            rename_terms = [term for term in list(signal_terms.get("naming_convention_violation") or []) if term]
            if len(rename_terms) >= 2:
                rename_phrase = f"并存在命名规范退化（{rename_terms[0]} -> {rename_terms[1]}）"
                rename_summary = f"变量/常量命名从 {rename_terms[0]} 退化为 {rename_terms[1]}，违背 Java 通用命名规范。"
            elif rename_terms:
                rename_phrase = f"并存在命名规范退化（{rename_terms[0]}）"
                rename_summary = f"标识符 {rename_terms[0]} 存在命名规范退化。"
            else:
                rename_phrase = "并存在命名规范退化"
                rename_summary = "当前改动引入了命名规范退化。"
            if "命名规范" not in title:
                title = f"{title}（命名规范退化）" if title else "命名规范退化"
            if rename_phrase.replace("并", "") not in claim:
                claim = f"{claim.rstrip('。')}；{rename_phrase}。".strip("；")
            if rename_summary not in summary_parts:
                summary_parts.append(rename_summary)
            if rename_phrase not in evidence:
                evidence.append(rename_phrase)

        if "exception_swallowed" in signal_set and "静默吞掉" not in claim_blob and "空 catch" not in claim_blob:
            swallow_phrase = "当前变更还让 catch 块静默吞掉异常"
            swallow_summary = "异常处理被弱化为静默吞掉异常，后续排障、补偿和审计都会变难。"
            claim = f"{claim.rstrip('。')}；{swallow_phrase}。".strip("；")
            if swallow_summary not in summary_parts:
                summary_parts.append(swallow_summary)
            if swallow_phrase not in evidence:
                evidence.append(swallow_phrase)

        result["title"] = title
        if summary_parts:
            result["summary"] = "；".join(part for part in summary_parts if part)
        result["claim"] = claim
        result["evidence"] = evidence
        return result

    def _stabilize_line_start(self, value: object, fallback: int, target_hunk: dict[str, object]) -> int:
        normalized = self._normalize_line_start(value, fallback)
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if changed_lines:
            min_changed = min(changed_lines)
            max_changed = max(changed_lines)
            if normalized < min_changed or normalized > max_changed:
                return min_changed
            return normalized
        start_line = self._normalize_optional_line_value(target_hunk.get("start_line"))
        end_line = self._normalize_optional_line_value(target_hunk.get("end_line")) or start_line
        if start_line is None:
            return normalized
        if normalized < start_line or (end_line is not None and normalized > end_line):
            return start_line
        return normalized

    def _stabilize_line_end(self, value: object, fallback: int, target_hunk: dict[str, object]) -> int:
        normalized = self._normalize_line_start(value, fallback)
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if changed_lines:
            return max(fallback, min(normalized, max(changed_lines)))
        end_line = self._normalize_optional_line_value(target_hunk.get("end_line"))
        if end_line is None:
            return max(fallback, normalized)
        return max(fallback, min(normalized, end_line))

    def _normalize_changed_line_values(self, values: object) -> list[int]:
        normalized: list[int] = []
        for item in list(values or []):
            parsed = self._normalize_optional_line_value(item)
            if parsed is not None:
                normalized.append(parsed)
        return normalized

    def _normalize_optional_line_value(self, value: object) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return max(1, parsed)

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

    def _build_runtime_tool_summary(self, runtime_tool_results: list[dict[str, object]]) -> str:
        """把运行时工具结果压缩成适合再次输入 LLM 的摘要。"""
        if not runtime_tool_results:
            return "无可用运行时工具或本轮未命中可调用工具。"
        lines: list[str] = []
        for item in runtime_tool_results:
            tool_name = str(item.get("tool_name") or "")
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"- {tool_name}: {summary}")
            else:
                lines.append(f"- {tool_name}: 已执行")
            matches = item.get("matches")
            if isinstance(matches, list) and matches:
                for match in matches[:2]:
                    if isinstance(match, dict):
                        title = str(match.get("title") or match.get("doc_id") or "knowledge")
                        snippet = str(match.get("snippet") or "").strip()
                        lines.append(f"  * {title}: {snippet[:160]}")
            if tool_name == "pg_schema_context":
                data_source_summary = item.get("data_source_summary")
                if isinstance(data_source_summary, dict):
                    database = str(data_source_summary.get("database") or "").strip()
                    host = str(data_source_summary.get("host") or "").strip()
                    schema_allowlist = [
                        str(value).strip()
                        for value in list(data_source_summary.get("schema_allowlist") or [])
                        if str(value).strip()
                    ]
                    source_line = "  * 数据源: "
                    source_line += database or "unknown_db"
                    if host:
                        source_line += f" @ {host}"
                    if schema_allowlist:
                        source_line += f" · schema={', '.join(schema_allowlist[:4])}"
                    lines.append(source_line)
                matched_tables = [
                    str(value).strip()
                    for value in list(item.get("matched_tables") or [])
                    if str(value).strip()
                ]
                if matched_tables:
                    lines.append(f"  * 命中表: {' / '.join(matched_tables[:6])}")
                table_columns = item.get("table_columns")
                if isinstance(table_columns, list) and table_columns:
                    formatted_columns: list[str] = []
                    for column in table_columns[:8]:
                        if not isinstance(column, dict):
                            continue
                        table_name = str(column.get("table_name") or "").strip()
                        column_name = str(column.get("column_name") or "").strip()
                        data_type = str(column.get("data_type") or "").strip()
                        nullable = str(column.get("is_nullable") or "").strip()
                        if table_name and column_name:
                            formatted_columns.append(
                                f"{table_name}.{column_name}({data_type or 'unknown'} / nullable={nullable or 'unknown'})"
                            )
                    if formatted_columns:
                        lines.append(f"  * 关键列: {' / '.join(formatted_columns[:6])}")
                constraints = item.get("constraints")
                if isinstance(constraints, list) and constraints:
                    formatted_constraints: list[str] = []
                    for constraint in constraints[:6]:
                        if not isinstance(constraint, dict):
                            continue
                        table_name = str(constraint.get("table_name") or "").strip()
                        constraint_type = str(constraint.get("constraint_type") or "").strip()
                        columns = str(constraint.get("columns") or "").strip()
                        if table_name and constraint_type:
                            formatted_constraints.append(f"{table_name}:{constraint_type}({columns})" if columns else f"{table_name}:{constraint_type}")
                    if formatted_constraints:
                        lines.append(f"  * 约束: {' / '.join(formatted_constraints[:5])}")
                indexes = item.get("indexes")
                if isinstance(indexes, list) and indexes:
                    formatted_indexes: list[str] = []
                    for index in indexes[:6]:
                        if not isinstance(index, dict):
                            continue
                        table_name = str(index.get("table_name") or "").strip()
                        indexname = str(index.get("indexname") or "").strip()
                        if table_name and indexname:
                            formatted_indexes.append(f"{table_name}:{indexname}")
                    if formatted_indexes:
                        lines.append(f"  * 索引: {' / '.join(formatted_indexes[:5])}")
                table_stats = item.get("table_stats")
                if isinstance(table_stats, list) and table_stats:
                    formatted_stats: list[str] = []
                    for stat in table_stats[:5]:
                        if not isinstance(stat, dict):
                            continue
                        table_name = str(stat.get("table_name") or "").strip()
                        estimated_rows = stat.get("estimated_rows")
                        total_size = str(stat.get("total_size") or "").strip()
                        if table_name:
                            row_part = f"rows≈{estimated_rows}" if estimated_rows not in (None, "") else "rows≈unknown"
                            size_part = f" size={total_size}" if total_size else ""
                            formatted_stats.append(f"{table_name}:{row_part}{size_part}")
                    if formatted_stats:
                        lines.append(f"  * 表统计: {' / '.join(formatted_stats[:4])}")
        return "\n".join(lines)

    def _build_repository_context_summary(
        self,
        repository_context: dict[str, object],
        runtime_tool_results: list[dict[str, object]],
    ) -> str:
        """整合主 Agent 和 repo_context_search 提供的代码仓上下文摘要。"""
        lines: list[str] = []
        if repository_context:
            summary = str(repository_context.get("summary") or "").strip()
            if summary:
                lines.append(f"- 主Agent上下文: {summary}")
            primary_context = repository_context.get("primary_context")
            if isinstance(primary_context, dict) and primary_context.get("snippet"):
                lines.append(f"- 目标文件: {primary_context.get('path')}")
                lines.extend(
                    f"    {line}"
                    for line in str(primary_context.get("snippet") or "").splitlines()[:12]
                    if str(line).strip()
                )
            related_contexts = repository_context.get("related_contexts")
            if isinstance(related_contexts, list) and related_contexts:
                lines.append("- 关联文件源码片段:")
                for item in related_contexts[:4]:
                    if not isinstance(item, dict):
                        continue
                    related_path = str(item.get("path") or "").strip()
                    related_snippet = str(item.get("snippet") or "").strip()
                    if not related_path or not related_snippet:
                        continue
                    lines.append(f"  * {related_path}")
                    lines.extend(f"    {line}" for line in related_snippet.splitlines()[:12])
            symbol_contexts = repository_context.get("symbol_contexts")
            if isinstance(symbol_contexts, list) and symbol_contexts:
                for item in symbol_contexts[:2]:
                    if not isinstance(item, dict):
                        continue
                    symbol = str(item.get("symbol") or "").strip()
                    definition_count = len(list(item.get("definitions") or []))
                    reference_count = len(list(item.get("references") or []))
                    if symbol:
                        lines.append(f"- 符号上下文: {symbol} · 定义 {definition_count} · 引用 {reference_count}")
                    definitions = item.get("definitions")
                    if isinstance(definitions, list) and definitions:
                        for definition in definitions[:2]:
                            if not isinstance(definition, dict):
                                continue
                            path = str(definition.get("path") or "").strip()
                            snippet = str(definition.get("snippet") or "").strip()
                            if not path or not snippet:
                                continue
                            lines.append(f"  * 定义: {path}")
                            lines.extend(f"    {line}" for line in snippet.splitlines()[:8])
                    references = item.get("references")
                    if isinstance(references, list) and references:
                        for reference in references[:2]:
                            if not isinstance(reference, dict):
                                continue
                            path = str(reference.get("path") or "").strip()
                            snippet = str(reference.get("snippet") or "").strip()
                            if not path or not snippet:
                                continue
                            lines.append(f"  * 引用: {path}")
                            lines.extend(f"    {line}" for line in snippet.splitlines()[:8])
            self._append_java_ddd_context_summary(lines, repository_context)
        for item in runtime_tool_results:
            if str(item.get("tool_name") or "") != "repo_context_search":
                continue
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"- Repo 工具: {summary}")
            context_files = [
                str(value).strip()
                for value in list(item.get("context_files") or [])
                if str(value).strip()
            ]
            if context_files:
                lines.append(f"- Repo 引用文件: {' / '.join(context_files[:4])}")
            matches = item.get("matches")
            if isinstance(matches, list) and matches:
                formatted = []
                for match in matches[:3]:
                    if isinstance(match, dict):
                        path = str(match.get("path") or "").strip()
                        line_number = match.get("line_number")
                        if path:
                            formatted.append(f"{path}:{line_number}" if line_number else path)
                if formatted:
                    lines.append(f"- 代码仓命中: {' / '.join(formatted)}")
            keyword_sources = item.get("search_keyword_sources")
            if isinstance(keyword_sources, list) and keyword_sources:
                formatted_keywords: list[str] = []
                for keyword_source in keyword_sources[:3]:
                    if not isinstance(keyword_source, dict):
                        continue
                    keyword = str(keyword_source.get("keyword") or "").strip()
                    source_label = str(keyword_source.get("source_label") or keyword_source.get("source") or "").strip()
                    if keyword and source_label:
                        formatted_keywords.append(f"{keyword}({source_label})")
                    elif keyword:
                        formatted_keywords.append(keyword)
                if formatted_keywords:
                    lines.append(f"- Repo 关键词来源: {' / '.join(formatted_keywords)}")
            symbol_contexts = item.get("symbol_contexts")
            if isinstance(symbol_contexts, list) and symbol_contexts:
                for symbol_context in symbol_contexts[:2]:
                    if not isinstance(symbol_context, dict):
                        continue
                    symbol = str(symbol_context.get("symbol") or "").strip()
                    if not symbol:
                        continue
                    lines.append(
                        f"- Repo 符号: {symbol} · 定义 {len(list(symbol_context.get('definitions') or []))} · "
                        f"引用 {len(list(symbol_context.get('references') or []))}"
                    )
            related_source_snippets = item.get("related_source_snippets")
            if isinstance(related_source_snippets, list) and related_source_snippets:
                lines.append("- 关联源码片段:")
                for snippet_item in related_source_snippets[:3]:
                    if not isinstance(snippet_item, dict):
                        continue
                    path = str(snippet_item.get("path") or "").strip()
                    kind = str(snippet_item.get("kind") or "").strip()
                    symbol = str(snippet_item.get("symbol") or "").strip()
                    snippet = str(snippet_item.get("snippet") or "").strip()
                    if not path or not snippet:
                        continue
                    header = path
                    if kind or symbol:
                        header += f"（{kind or 'context'} / {symbol or 'n/a'}）"
                    lines.append(f"  * {header}")
                    lines.extend(f"    {line}" for line in snippet.splitlines()[:8])
            self._append_java_ddd_context_summary(lines, item)
        return "\n".join(lines) if lines else "未补充代码仓上下文。"

    def _build_repository_source_blocks(
        self,
        repository_context: dict[str, object],
        runtime_tool_results: list[dict[str, object]],
    ) -> str:
        lines: list[str] = []
        primary_context = repository_context.get("primary_context")
        if isinstance(primary_context, dict):
            path = str(primary_context.get("path") or "").strip()
            snippet = str(primary_context.get("snippet") or "").strip()
            if path and snippet:
                lines.append(f"# 目标文件源码\n{path}")
                lines.extend(snippet.splitlines()[:20])

        current_class_context = repository_context.get("current_class_context")
        if isinstance(current_class_context, dict):
            path = str(current_class_context.get("path") or "").strip()
            snippet = str(current_class_context.get("snippet") or "").strip()
            if path and snippet:
                if lines:
                    lines.append("")
                lines.append(f"# 当前类问题片段\n{path}")
                lines.extend(snippet.splitlines()[:20])

        for key, label in [
            ("parent_contract_contexts", "父接口/抽象类"),
            ("caller_contexts", "调用方"),
            ("callee_contexts", "被调方"),
            ("domain_model_contexts", "领域模型"),
            ("persistence_contexts", "持久化上下文"),
        ]:
            contexts = repository_context.get(key)
            if not isinstance(contexts, list):
                continue
            appended = 0
            for item in contexts[:2]:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                snippet = str(item.get("snippet") or "").strip()
                symbol = str(item.get("symbol") or "").strip()
                if not path or not snippet:
                    continue
                if lines:
                    lines.append("")
                header = f"# {label}\n{path}"
                if symbol:
                    header += f" · {symbol}"
                lines.append(header)
                lines.extend(snippet.splitlines()[:16])
                appended += 1
            if appended:
                continue

        transaction_context = repository_context.get("transaction_context")
        if isinstance(transaction_context, dict) and transaction_context:
            snippet = str(transaction_context.get("transaction_boundary_snippet") or "").strip()
            path = str(transaction_context.get("transactional_path") or "").strip()
            method_name = str(transaction_context.get("transactional_method") or "").strip()
            if snippet:
                if lines:
                    lines.append("")
                header = f"# 事务边界\n{path}"
                if method_name:
                    header += f" · {method_name}"
                lines.append(header)
                lines.extend(snippet.splitlines()[:16])
            call_chain = [
                str(item).strip()
                for item in list(transaction_context.get("call_chain") or [])
                if str(item).strip()
            ]
            if call_chain:
                lines.append(f"调用链: {' -> '.join(call_chain[:6])}")

        if not lines:
            for item in runtime_tool_results:
                if str(item.get("tool_name") or "") != "repo_context_search":
                    continue
                primary = item.get("primary_context")
                if isinstance(primary, dict):
                    path = str(primary.get("path") or "").strip()
                    snippet = str(primary.get("snippet") or "").strip()
                    if path and snippet:
                        lines.append(f"# 目标文件源码\n{path}")
                        lines.extend(snippet.splitlines()[:20])
                        break
        return "\n".join(lines) if lines else "未补充可直接供大模型阅读的源码上下文。"

    def _append_java_ddd_context_summary(self, lines: list[str], context_payload: dict[str, object]) -> None:
        java_review_mode = str(context_payload.get("java_review_mode") or "").strip()
        java_context_signals = [
            str(item).strip()
            for item in list(context_payload.get("java_context_signals") or [])
            if str(item).strip()
        ]
        if java_review_mode:
            mode_label = "Java DDD 增强模式" if java_review_mode == "ddd_enhanced" else "Java 通用模式"
            lines.append(f"- Java 审查模式: {mode_label}")
        if java_context_signals:
            lines.append(f"- Java 结构信号: {' / '.join(java_context_signals[:8])}")
        java_quality_signals = [
            str(item).strip()
            for item in list(context_payload.get("java_quality_signals") or [])
            if str(item).strip()
        ]
        if java_quality_signals:
            lines.append(f"- Java 通用质量信号: {' / '.join(java_quality_signals[:8])}")
        java_quality_signal_summary = str(context_payload.get("java_quality_signal_summary") or "").strip()
        if java_quality_signal_summary:
            lines.append(f"- Java 通用质量摘要: {java_quality_signal_summary}")
        current_class_context = context_payload.get("current_class_context")
        if isinstance(current_class_context, dict) and current_class_context.get("snippet"):
            lines.append("- Java 当前类问题片段:")
            lines.append(
                f"  * {str(current_class_context.get('path') or '').strip()} "
                f"{str(current_class_context.get('class_name') or '').strip()}::"
                f"{str(current_class_context.get('method_name') or '').strip()}"
            )
            lines.extend(
                f"    {line}"
                for line in str(current_class_context.get("snippet") or "").splitlines()[:14]
                if str(line).strip()
            )
        for key, label in [
            ("parent_contract_contexts", "父接口/抽象类"),
            ("caller_contexts", "调用方"),
            ("callee_contexts", "被调方"),
            ("domain_model_contexts", "领域模型"),
            ("persistence_contexts", "持久化上下文"),
        ]:
            contexts = context_payload.get(key)
            if not isinstance(contexts, list) or not contexts:
                continue
            lines.append(f"- Java {label}:")
            for item in contexts[:3]:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                snippet = str(item.get("snippet") or "").strip()
                symbol = str(item.get("symbol") or "").strip()
                if not path or not snippet:
                    continue
                header = path
                if symbol:
                    header += f" · {symbol}"
                lines.append(f"  * {header}")
                lines.extend(f"    {line}" for line in snippet.splitlines()[:10])
        transaction_context = context_payload.get("transaction_context")
        if isinstance(transaction_context, dict) and transaction_context:
            lines.append("- Java 事务边界:")
            method_name = str(transaction_context.get("transactional_method") or "").strip()
            transaction_path = str(transaction_context.get("transactional_path") or "").strip()
            if transaction_path or method_name:
                lines.append(f"  * {transaction_path} · {method_name}")
            boundary_snippet = str(transaction_context.get("transaction_boundary_snippet") or "").strip()
            if boundary_snippet:
                lines.extend(f"    {line}" for line in boundary_snippet.splitlines()[:10])
            call_chain = [str(item).strip() for item in list(transaction_context.get("call_chain") or []) if str(item).strip()]
            if call_chain:
                lines.append(f"  * 调用链: {' -> '.join(call_chain[:6])}")

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
        for signal in list(java_quality.get("signals") or [])[:8]:
            normalized = str(signal).strip()
            if normalized:
                query_terms.append(f"java_quality:{normalized}")
        for term in list(java_quality.get("matched_terms") or [])[:8]:
            normalized = str(term).strip()
            if normalized:
                query_terms.append(f"java_term:{normalized}")
        return {
            "changed_files": list(subject.changed_files),
            "query_terms": query_terms,
            "knowledge_sources": list(expert.knowledge_sources or []),
            "focus_file": file_path,
            "focus_line": line_start,
        }

    def _build_review_spec_summary(self, review_spec: str) -> str:
        if not review_spec.strip():
            return "未提供额外规范文档，请至少遵守职责边界、证据优先、修复建议可执行三条规则。"
        lines = [line.strip() for line in review_spec.splitlines() if line.strip()]
        return "\n".join(lines[:18])

    def _build_bound_documents_summary(self, bound_documents: list[object]) -> str:
        if not bound_documents:
            return "未绑定额外专家参考文档。"
        lines: list[str] = []
        for item in bound_documents[:8]:
            title = str(getattr(item, "title", "") or "").strip() or "未命名文档"
            doc_type = str(getattr(item, "doc_type", "") or "reference").strip()
            source_filename = str(getattr(item, "source_filename", "") or "").strip()
            tags = [str(tag).strip() for tag in list(getattr(item, "tags", []) or []) if str(tag).strip()]
            line = f"- [{doc_type}] {title}"
            if source_filename:
                line += f" · {source_filename}"
            if tags:
                line += f" · 标签: {' / '.join(tags[:4])}"
            matched_sections = list(getattr(item, "matched_sections", []) or [])
            outline = [str(value).strip() for value in list(getattr(item, "indexed_outline", []) or []) if str(value).strip()]
            if matched_sections:
                matched_paths = [
                    str(getattr(section, "path", "") or "").strip()
                    for section in matched_sections[:2]
                    if str(getattr(section, "path", "") or "").strip()
                ]
                if matched_paths:
                    line += f" · 命中章节: {' / '.join(matched_paths)}"
            elif outline:
                line += f" · 章节索引: {' / '.join(outline[:3])}"
            lines.append(line)
        return "\n".join(lines)

    def _build_bound_documents_fulltext(self, bound_documents: list[object]) -> str:
        if not bound_documents:
            return "《专家绑定参考文档》开始\n未绑定额外专家参考文档。\n《专家绑定参考文档》结束"
        sections: list[str] = ["《专家绑定参考文档》开始"]
        for index, item in enumerate(bound_documents, start=1):
            title = str(getattr(item, "title", "") or "").strip() or f"文档 {index}"
            doc_type = str(getattr(item, "doc_type", "") or "reference").strip()
            source_filename = str(getattr(item, "source_filename", "") or "").strip()
            matched_sections = list(getattr(item, "matched_sections", []) or [])
            outline = [str(value).strip() for value in list(getattr(item, "indexed_outline", []) or []) if str(value).strip()]
            sections.append(f"## 文档 {index}: {title}")
            sections.append(f"- 类型: {doc_type}")
            if source_filename:
                sections.append(f"- 来源文件: {source_filename}")
            if matched_sections:
                sections.append("- 命中章节如下：")
                for section in matched_sections[:6]:
                    path = str(getattr(section, "path", "") or "").strip() or str(getattr(section, "title", "") or "").strip()
                    summary = str(getattr(section, "summary", "") or "").strip()
                    content = str(getattr(section, "content", "") or "").strip() or "空章节"
                    sections.append(f"### {path}")
                    if summary:
                        sections.append(f"摘要: {summary}")
                    sections.append(content[:1600])
            elif outline:
                sections.append("- 未命中具体章节，以下为文档目录索引：")
                sections.extend([f"  - {value}" for value in outline[:12]])
            else:
                content = str(getattr(item, "content", "") or "").strip() or "空文档"
                sections.append(content[:2000])
        sections.append("《专家绑定参考文档》结束")
        return "\n".join(sections)

    def _build_rule_screening_summary(self, rule_screening: dict[str, object]) -> str:
        total_rules = int(rule_screening.get("total_rules") or 0)
        if total_rules <= 0:
            return "当前未绑定可执行规则卡。"
        must_review_count = int(rule_screening.get("must_review_count") or 0)
        possible_hit_count = int(rule_screening.get("possible_hit_count") or 0)
        lines = [
            f"- 已遍历规则: {total_rules}",
            f"- 强命中规则: {must_review_count}",
            f"- 候选规则: {possible_hit_count}",
        ]
        matched_rules = list(rule_screening.get("matched_rules_for_llm", []) or [])
        if matched_rules:
            lines.append("- 本轮优先带入审查的规则:")
            for item in matched_rules[:5]:
                title = str(item.get("title") or item.get("rule_id") or "").strip()
                priority = str(item.get("priority") or "P2").strip()
                scene_path = str(item.get("scene_path") or "").strip()
                reason = str(item.get("reason") or "").strip()
                if title:
                    label = f"[{priority}] {title}"
                    if scene_path:
                        label = f"{label}（{scene_path}）"
                    lines.append(f"  - {label} · {reason or '命中规则信号'}")
        return "\n".join(lines)

    def _build_rule_screening_fulltext(self, rule_screening: dict[str, object]) -> str:
        total_rules = int(rule_screening.get("total_rules") or 0)
        if total_rules <= 0:
            return "《规则遍历结果》开始\n当前未绑定可执行规则卡。\n《规则遍历结果》结束"
        sections = [
            "《规则遍历结果》开始",
            f"- 已遍历规则总数: {total_rules}",
            f"- 强命中规则数: {int(rule_screening.get('must_review_count') or 0)}",
            f"- 候选规则数: {int(rule_screening.get('possible_hit_count') or 0)}",
        ]
        matched_rules = list(rule_screening.get("matched_rules_for_llm", []) or [])
        if matched_rules:
            sections.append("- 本轮应优先遵守并逐条核查的规则卡：")
            for item in matched_rules[:6]:
                title = str(item.get("title") or item.get("rule_id") or "").strip()
                priority = str(item.get("priority") or "P2").strip()
                scene_path = str(item.get("scene_path") or "").strip()
                description = str(item.get("description") or "").strip()
                language = str(item.get("language") or "").strip()
                matched_terms = [
                    str(value).strip()
                    for value in list(item.get("matched_terms", []) or [])[:6]
                    if str(value).strip()
                ]
                sections.append(f"## [{priority}] {title}")
                if scene_path:
                    sections.append(f"场景路径: {scene_path}")
                if description:
                    sections.append(f"规则描述: {description}")
                if language:
                    sections.append(f"语言: {language}")
                if matched_terms:
                    sections.append(f"命中关键词: {' / '.join(matched_terms)}")
                problem_code_example = str(item.get("problem_code_example") or "").strip()
                problem_code_line = str(item.get("problem_code_line") or "").strip()
                false_positive_code = str(item.get("false_positive_code") or "").strip()
                if problem_code_example:
                    sections.append("问题代码示例:")
                    sections.append(problem_code_example[:800])
                if problem_code_line:
                    sections.append(f"重点关注代码行模式: {problem_code_line[:500]}")
                if false_positive_code:
                    sections.append("误报代码参考:")
                    sections.append(false_positive_code[:800])
        sections.append("《规则遍历结果》结束")
        return "\n".join(sections)

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
            "signal_summary": str(tool_result.get("signal_summary") or tool_result.get("java_quality_signal_summary") or "").strip(),
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

    def _is_meaningful_context_file(self, path_text: str) -> bool:
        normalized = str(path_text or "").strip().replace("\\", "/")
        if not normalized:
            return False
        banned_parts = [".git/", "node_modules/", "dist/", "build/", ".next/", ".turbo/", "__pycache__/"]
        if any(part in normalized for part in banned_parts):
            return False
        banned_suffixes = (".lock", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2")
        return not normalized.endswith(banned_suffixes)

    def _should_skip_finding(self, expert_id: str, finding: ReviewFinding) -> bool:
        if self._looks_like_non_issue_finding(finding):
            logger.info(
                "suppressing non-issue finding review_id=%s expert_id=%s file=%s line=%s title=%s",
                finding.review_id,
                expert_id,
                finding.file_path,
                finding.line_start,
                finding.title,
            )
            return True
        if expert_id != "performance_reliability":
            return False
        text_blob = "\n".join(
            [
                finding.title,
                finding.summary,
                finding.rule_based_reasoning,
                *finding.evidence,
                *finding.cross_file_evidence,
            ]
        ).lower()
        perf_tokens = {
            "超时",
            "重试",
            "限流",
            "吞吐",
            "锁",
            "热点",
            "退化",
            "并发",
            "序列化",
            "响应体",
            "缓存",
            "内存",
            "cpu",
            "latency",
            "throughput",
            "timeout",
            "retry",
            "cache",
            "performance",
        }
        has_perf_signal = any(token in text_blob for token in perf_tokens)
        has_repo_context = len(finding.context_files) >= 2
        if finding.finding_type == "risk_hypothesis" and (not has_perf_signal or not has_repo_context):
            logger.info(
                "suppressing weak performance finding review_id=%s file=%s line=%s has_perf_signal=%s has_repo_context=%s title=%s",
                finding.review_id,
                finding.file_path,
                finding.line_start,
                has_perf_signal,
                has_repo_context,
                finding.title,
            )
            return True
        return False

    def _looks_like_non_issue_finding(self, finding: ReviewFinding) -> bool:
        text_blob = "\n".join(
            [
                finding.title,
                finding.summary,
                finding.rule_based_reasoning,
                *finding.evidence,
                *finding.cross_file_evidence,
            ]
        ).lower()
        no_issue_phrases = {
            "无风险",
            "没有风险",
            "无架构风险",
            "无可维护性风险",
            "无需处理",
            "保持现状",
            "仅涉及格式化",
            "仅为格式化",
            "仅是格式化",
            "代码格式化",
            "缩进调整",
            "空格调整",
            "换行调整",
        }
        formatting_tokens = {"formatting", "format only", "whitespace", "indent", "reformat"}
        has_no_issue_phrase = any(token in text_blob for token in no_issue_phrases | formatting_tokens)
        if not has_no_issue_phrase:
            return False
        if finding.severity not in {"low", "medium"}:
            return False
        return True

    def _build_debate_prompt(
        self,
        subject: ReviewSubject,
        issue: DebateIssue,
        expert: ExpertProfile,
        reply_to_expert_id: str,
        file_path: str,
        line_start: int,
        bound_documents: list[object],
    ) -> str:
        code_excerpt = self._build_code_excerpt(subject, file_path, line_start, expert.expert_id)
        bound_documents_summary = self._build_bound_documents_summary(bound_documents)
        return (
            f"议题标题: {issue.title}\n"
            f"议题摘要: {issue.summary}\n"
            f"当前专家: {expert.expert_id} / {expert.name_zh}\n"
            f"你要回应的对象: {reply_to_expert_id}\n"
            f"目标代码: {file_path}:{line_start}\n"
            f"职责边界: {' / '.join(expert.focus_areas) or expert.role}\n"
            f"禁止越界: {' / '.join(expert.out_of_scope) or '不要替其他专家下最终结论'}\n"
            f"已绑定参考文档:\n{bound_documents_summary}\n"
            f"代码片段:\n{code_excerpt}\n"
            f"请输出一段中文聊天式辩论消息，必须围绕 {file_path}:{line_start} 这段真实变更展开，"
            f"先点名回应对象，再说明你同意或反驳什么，指出具体代码证据，并说明还缺什么验证。"
        )

    def _build_debate_fallback(
        self,
        issue: DebateIssue,
        expert: ExpertProfile,
        reply_to_expert_id: str,
        file_path: str,
        line_start: int,
    ) -> str:
        return (
            f"回应 @{reply_to_expert_id}：我继续看了 {file_path}:{line_start}。"
            f" 对于“{issue.title}”这个议题，我认为争议点不只是 {issue.summary}，"
            f" 还要确认这里的边界条件和回退路径是否被覆盖，否则这个风险还不能直接关闭。"
        )

    def _extract_structured_field(self, text: str, label: str) -> str:
        marker = f"{label}："
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith(marker):
                return line.split(marker, 1)[1].strip()
        return ""

    def _parse_json_object(self, text: str) -> dict[str, object]:
        content = text.strip()
        candidates = [content]
        if "```json" in content:
            fragment = content.split("```json", 1)[1].split("```", 1)[0].strip()
            candidates.insert(0, fragment)
        if "```" in content and len(candidates) == 1:
            fragment = content.split("```", 1)[1].split("```", 1)[0].strip()
            candidates.insert(0, fragment)
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(content[start : end + 1])
            except Exception:
                return {}
            if isinstance(payload, dict):
                return payload
        return {}

    def _normalize_severity(self, value: object, fallback: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"blocker", "critical"}:
            return "blocker"
        if normalized in {"high", "medium", "low"}:
            return normalized
        return fallback

    def _normalize_confidence(self, value: object, fallback: float) -> float:
        try:
            parsed = float(value)
        except Exception:
            return fallback
        return min(0.99, max(0.01, parsed))

    def _normalize_text_list(self, value: object, fallback: list[str]) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            chunks = [item.strip() for item in value.split("\n") if item.strip()]
            return chunks or [str(item).strip() for item in fallback if str(item).strip()]
        return [str(item).strip() for item in fallback if str(item).strip()]

    def _normalize_line_start(self, value: object, fallback: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            return fallback
        return max(1, parsed)

    def _extract_summary(self, text: str, fallback: str) -> str:
        normalized = " ".join(text.replace("\n", " ").split())
        if not normalized:
            return fallback
        if len(normalized) <= 96:
            return normalized
        return normalized[:96].rstrip("，,。.;；:：") + "。"

    def _allow_llm_fallback(self, runtime_settings) -> bool:
        return bool(getattr(runtime_settings, "allow_llm_fallback", False) or os.getenv("PYTEST_CURRENT_TEST"))

    def _resolve_analysis_mode(self, review: ReviewTask, runtime_settings) -> Literal["standard", "light"]:
        mode = str(getattr(review, "analysis_mode", "") or getattr(runtime_settings, "default_analysis_mode", "") or "standard").strip().lower()
        if mode not in {"standard", "light"}:
            return "standard"
        return mode  # type: ignore[return-value]

    def _effective_runtime_settings(self, runtime_settings, analysis_mode: Literal["standard", "light"]):
        if analysis_mode != "light":
            return runtime_settings
        return runtime_settings.model_copy(
            update={
                "default_max_debate_rounds": min(
                    int(getattr(runtime_settings, "default_max_debate_rounds", 1) or 1),
                    int(getattr(runtime_settings, "light_max_debate_rounds", 1) or 1),
                ),
            }
        )

    def _build_llm_request_options(
        self,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
    ) -> dict[str, int | float]:
        if analysis_mode == "light":
            return {
                "timeout_seconds": max(30, int(getattr(runtime_settings, "light_llm_timeout_seconds", 120) or 120)),
                "max_attempts": max(1, int(getattr(runtime_settings, "light_llm_retry_count", 2) or 2)),
            }
        return {
            "timeout_seconds": max(20, int(getattr(runtime_settings, "standard_llm_timeout_seconds", 60) or 60)),
            "max_attempts": max(1, int(getattr(runtime_settings, "standard_llm_retry_count", 3) or 3)),
        }

    def _max_parallel_experts(
        self,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
    ) -> int:
        if analysis_mode == "light":
            return max(1, int(getattr(runtime_settings, "light_max_parallel_experts", 1) or 1))
        return max(1, int(getattr(runtime_settings, "standard_max_parallel_experts", 4) or 4))
