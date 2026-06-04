from __future__ import annotations

import threading
import os
import multiprocessing as mp
import logging
import json
import hashlib
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.domain.models.event import ReviewEvent
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.feedback import FeedbackLabel
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.knowledge import KnowledgeDocument
from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.review_skill import ReviewSkillProfile
from app.domain.models.review_tool_plugin import ReviewToolPlugin
from app.domain.models.runtime_settings import RuntimeSettings
from app.logging_config import configure_logging as configure_app_logging
from app.repositories.storage_factory import StorageRepositoryFactory
from app.services.artifact_service import ArtifactService, build_report_summary
from app.services.expert_registry import ExpertRegistry
from app.services.extension_editor_service import ExtensionEditorService
from app.services.feedback_learner_service import FeedbackLearnerService
from app.services.review_learning_service import ReviewLearningService
from app.services.change_impact_report_service import ChangeImpactReportService
from app.services.knowledge_service import KnowledgeService
from app.services.gitnexus_impact_service import GitNexusImpactService
from app.services.platform_adapter import OpenMergeRequest, PlatformAdapter
from app.services.repository_config_resolver import RepositoryConfigResolver
from app.services.repository_context_service import RepositoryContextService
from app.services.review_service_projection import ReviewServiceProjectionMixin
from app.services.review_service_report import ReviewServiceReportMixin
from app.services.review_runner import ReviewClosedError, ReviewRunner
from app.services.runtime_settings_service import RuntimeSettingsService

logger = logging.getLogger(__name__)
DEFAULT_MR_EXPERTS = ("change_impact_analysis",)


def _is_formal_issue(issue: DebateIssue) -> bool:
    """Return whether an issue should appear in the effective issue list."""

    status = str(issue.status or "").strip().lower()
    resolution = str(issue.resolution or "").strip().lower()
    human_decision = str(issue.human_decision or "").strip().lower()
    if human_decision == "rejected" or resolution == "human_rejected":
        return False
    if status in {"needs_verification", "comment", "abstain", "rejected_after_debate"}:
        return False
    if resolution in {
        "needs_verification",
        "llm_judge_needs_verification",
        "targeted_debate_needs_verification",
        "feedback_profile_requires_more_evidence",
        "comment",
        "abstain",
    }:
        return False
    return True


def parse_json_object(value: str) -> dict[str, object]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_review_in_subprocess(storage_root: str, review_id: str) -> None:
    """子进程执行审核主流程，隔离潜在的阻塞/死锁风险。"""
    from app.services.review_runner import ReviewRunner, ReviewClosedError

    configure_app_logging(settings.LOGS_ROOT)
    runner = ReviewRunner(Path(storage_root))
    try:
        runner.run_once(review_id)
    except ReviewClosedError:
        return
    finally:
        runner.clear_runtime_caches()


class ReviewService(ReviewServiceProjectionMixin, ReviewServiceReportMixin):
    """审核应用层总入口。

    这层面向 API 和页面交互，负责把“创建任务、启动执行、查询结果、管理专家/知识库”
    这些应用动作统一收口。真正的专家分析和裁决由 ReviewRunner 完成，
    这里更像一个把配置、平台适配、仓储和运行时串起来的协调层。
    """

    def __init__(self, storage_root: Path | None = None) -> None:
        self.storage_root = Path(storage_root or settings.STORAGE_ROOT)
        self._logs_root = Path(settings.LOGS_ROOT)
        self.review_repo = None
        self.event_repo = None
        self.feedback_repo = None
        self.finding_repo = None
        self.issue_repo = None
        self.message_repo = None
        self.runner = None
        self.artifact_service = ArtifactService(self.storage_root)
        self.expert_registry = ExpertRegistry(self.storage_root / "experts")
        self.feedback_learner_service = None
        self.review_learning_service = None
        self.knowledge_service = None
        self.runtime_settings_service = RuntimeSettingsService(self.storage_root)
        self.gitnexus_impact_service = GitNexusImpactService(self.storage_root)
        self.platform_adapter = PlatformAdapter()
        self.repository_resolver = RepositoryConfigResolver()
        self.extension_editor_service = ExtensionEditorService(Path(__file__).resolve().parents[3])
        self._active_reviews: set[str] = set()
        self._active_reviews_lock = threading.Lock()
        self._active_review_processes: dict[str, mp.Process] = {}
        self._active_review_processes_lock = threading.Lock()
        self._db_compaction_running = False
        self._db_compaction_pending = False
        self._db_compaction_lock = threading.Lock()
        self._reload_storage_services()

    def _reload_storage_services(self) -> None:
        repository_factory = StorageRepositoryFactory(self.storage_root)
        self.review_repo = repository_factory.create_review_repository()
        self.event_repo = repository_factory.create_event_repository()
        self.feedback_repo = repository_factory.create_feedback_repository()
        self.finding_repo = repository_factory.create_finding_repository()
        self.issue_repo = repository_factory.create_issue_repository()
        self.message_repo = repository_factory.create_message_repository()
        self.runner = ReviewRunner(self.storage_root)
        self.feedback_learner_service = FeedbackLearnerService(self.storage_root)
        self.review_learning_service = ReviewLearningService(self.storage_root)
        self.knowledge_service = KnowledgeService(self.storage_root)
        self.knowledge_service.bootstrap_builtin_documents()

    def create_review(self, payload: dict[str, object]) -> ReviewTask:
        """创建审核任务并落盘为 pending。

        这里会优先补全：
        - 默认分析模式
        - 按平台选择的 Git access token
        - 由 PlatformAdapter 归一化后的 ReviewSubject
        """
        review_id = f"rev_{uuid4().hex[:8]}"
        runtime_settings = self.get_runtime_settings()
        analysis_mode = str(payload.pop("analysis_mode", runtime_settings.default_analysis_mode or "standard")).strip()
        if analysis_mode not in {"standard", "light"}:
            analysis_mode = runtime_settings.default_analysis_mode or "standard"
        requested_experts = [
            str(expert_id).strip()
            for expert_id in payload.pop("selected_experts", []) or []
            if str(expert_id).strip()
        ]
        request_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        manual_expert_selection = (
            bool(request_metadata.get("manual_expert_selection"))
            if "manual_expert_selection" in request_metadata
            else bool(requested_experts)
        )
        selected_experts = list(requested_experts)
        design_docs = [
            item
            for item in payload.pop("design_docs", []) or []
            if isinstance(item, dict) and str(item.get("content") or "").strip()
        ]
        requested_target_ref = str(payload.get("target_ref") or "").strip()
        if not str(payload.get("access_token") or "").strip():
            review_url = str(payload.get("mr_url") or payload.get("repo_url") or "")
            configured_token = self._resolve_git_access_token(review_url, runtime_settings)
            if configured_token:
                payload["access_token"] = configured_token
        subject = self.platform_adapter.normalize(ReviewSubject.model_validate(payload), runtime_settings)
        if not str(subject.project_id or "").strip():
            subject.project_id = str(runtime_settings.default_project_id or "").strip()
        incoming_metadata = dict(subject.metadata or {})
        incoming_workspace_repo_path = str(incoming_metadata.get("workspace_repo_path") or "").strip()
        resolved_repository = self.repository_resolver.resolve(runtime_settings, subject)
        if subject.subject_type == "mr":
            for expert_id in DEFAULT_MR_EXPERTS:
                if expert_id not in selected_experts:
                    selected_experts.append(expert_id)
        subject.metadata = {
            **incoming_metadata,
            "repository_id": resolved_repository.repository_id,
            "repository_name": resolved_repository.name,
            "repository_provider": resolved_repository.provider,
            "workspace_repo_path": incoming_workspace_repo_path or resolved_repository.local_path,
            "configured_workspace_repo_path": resolved_repository.local_path,
            "repository_clone_url": resolved_repository.clone_url,
            "manual_expert_selection": manual_expert_selection,
            # API 直接创建且缺少 diff/changed_files 时，允许走兜底派工，避免回放/报告页完全无数据。
            "allow_empty_diff_fallback": bool(incoming_metadata.get("allow_empty_diff_fallback", True)),
        }
        subject.repo_id = subject.repo_id or resolved_repository.repository_id
        subject.repo_url = subject.repo_url or resolved_repository.clone_url
        subject.target_ref = requested_target_ref or resolved_repository.default_branch or subject.target_ref
        if design_docs:
            subject.metadata = {
                **subject.metadata,
                "design_docs": [
                    {
                        "doc_id": str(item.get("doc_id") or f"design_{uuid4().hex[:8]}"),
                        "title": str(item.get("title") or item.get("filename") or "详细设计文档"),
                        "filename": str(item.get("filename") or "design-spec.md"),
                        "content": str(item.get("content") or ""),
                        "doc_type": "design_spec",
                    }
                    for item in design_docs
                ],
            }
        task = ReviewTask(
            review_id=review_id,
            subject=subject,
            status="pending",
            phase="pending",
            analysis_mode=analysis_mode,
            selected_experts=selected_experts,
        )
        self.review_repo.save(task)
        logger.info(
            "review created review_id=%s subject_type=%s analysis_mode=%s mr_url=%s selected_experts=%s",
            review_id,
            task.subject.subject_type,
            task.analysis_mode,
            task.subject.mr_url,
            task.selected_experts,
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="review_created",
                phase="pending",
                message="审核任务已创建",
            )
        )
        return task

    def start_review(self, review_id: str) -> ReviewTask:
        """同步执行一次审核。

        主要用于测试或阻塞式触发；前端交互默认优先走异步启动。
        """
        try:
            return self.runner.run_once(review_id)
        except Exception as exc:
            return self._mark_failed(review_id, str(exc))

    def start_review_async(self, review_id: str) -> ReviewTask:
        """异步启动审核并立即返回 queued/running 状态。

        这样前端在点击“创建并启动审核”后，可以第一时间跳转到过程页，
        再通过轮询/SSE 逐步看到主 Agent 和专家消息流。
        """
        if os.getenv("PYTEST_CURRENT_TEST"):
            return self.start_review(review_id)
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        if review.status in {"running", "waiting_human", "completed"}:
            logger.info("review start skipped review_id=%s status=%s", review_id, review.status)
            return review
        with self._active_reviews_lock:
            if review_id in self._active_reviews:
                refreshed = self.get_review(review_id)
                logger.info("review already active review_id=%s", review_id)
                return refreshed or review
            self._active_reviews.add(review_id)

        review.status = "running"
        review.phase = "queued"
        if review.started_at is None:
            review.started_at = datetime.now(UTC)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        logger.info(
            "review queued review_id=%s phase=%s analysis_mode=%s selected_experts=%s",
            review_id,
            review.phase,
            review.analysis_mode,
            review.selected_experts,
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="review_queued",
                phase="queued",
                message="审核任务已进入执行队列",
            )
        )

        def _run_in_background() -> None:
            try:
                logger.info("review background execution started review_id=%s", review_id)
                self.runner.run_once(review_id)
            except ReviewClosedError:
                logger.info("review background execution stopped because review was closed review_id=%s", review_id)
            except Exception as exc:
                logger.exception("review background execution failed review_id=%s error=%s", review_id, exc)
                self._mark_failed(review_id, str(exc))
            finally:
                self.runner.clear_runtime_caches()
                with self._active_reviews_lock:
                    self._active_reviews.discard(review_id)
                self._try_schedule_deferred_compaction()
                logger.info("review background execution finished review_id=%s", review_id)

        use_subprocess = self._use_subprocess_runner()
        if use_subprocess:
            started = self._start_review_subprocess(review_id)
            if not started:
                logger.warning("review subprocess start failed, fallback to background thread review_id=%s", review_id)
                threading.Thread(target=_run_in_background, daemon=True).start()
        else:
            threading.Thread(target=_run_in_background, daemon=True).start()
        return review

    def _use_subprocess_runner(self) -> bool:
        flag = str(os.getenv("REVIEW_RUNNER_SUBPROCESS", "")).strip().lower()
        if flag in {"1", "true", "on", "yes"}:
            return True
        if flag in {"0", "false", "off", "no"}:
            return False
        # 默认开启子进程隔离，避免单任务异常影响主服务。
        return True

    def _start_review_subprocess(self, review_id: str) -> bool:
        """在子进程执行审核，避免主进程被长时间阻塞。"""
        try:
            process_ctx = mp.get_context("spawn")
            process = process_ctx.Process(
                target=_run_review_in_subprocess,
                args=(str(self.storage_root), review_id),
                name=f"review-worker-{review_id}",
                daemon=False,
            )
            process.start()
        except Exception as exc:
            logger.exception("failed to start review subprocess review_id=%s error=%s", review_id, exc)
            return False
        with self._active_review_processes_lock:
            self._active_review_processes[review_id] = process
        logger.info("review subprocess started review_id=%s pid=%s", review_id, process.pid)
        try:
            review = self.get_review(review_id)
            if review is not None:
                metadata = dict(review.subject.metadata or {})
                metadata["execution_mode"] = "subprocess"
                metadata["worker_pid"] = int(process.pid or 0)
                metadata["worker_started_at"] = datetime.now(UTC).isoformat()
                review.subject.metadata = metadata
                review.updated_at = datetime.now(UTC)
                self.review_repo.save(review)
        except Exception as exc:
            logger.warning("failed to persist subprocess metadata review_id=%s error=%s", review_id, exc)

        def _watch_process() -> None:
            max_seconds = self._subprocess_max_runtime_seconds()
            started_at = time.monotonic()
            try:
                while process.is_alive():
                    process.join(timeout=1.0)
                    if max_seconds > 0 and time.monotonic() - started_at > float(max_seconds):
                        logger.error(
                            "review subprocess timeout exceeded review_id=%s pid=%s timeout_seconds=%s",
                            review_id,
                            process.pid,
                            max_seconds,
                        )
                        self._terminate_process(process, review_id=review_id, reason="timeout_exceeded")
                        break
                exit_code = int(process.exitcode or 0)
                if exit_code != 0:
                    latest = self.get_review(review_id)
                    if latest is not None and latest.status == "running":
                        self._mark_failed(review_id, f"review subprocess exited unexpectedly: code={exit_code}")
                    logger.error("review subprocess exited abnormally review_id=%s exit_code=%s", review_id, exit_code)
            finally:
                with self._active_review_processes_lock:
                    self._active_review_processes.pop(review_id, None)
                self.runner.clear_runtime_caches()
                with self._active_reviews_lock:
                    self._active_reviews.discard(review_id)
                self._try_schedule_deferred_compaction()
                logger.info("review subprocess finished review_id=%s", review_id)

        threading.Thread(target=_watch_process, daemon=True).start()
        return True

    def _subprocess_max_runtime_seconds(self) -> int:
        raw = str(os.getenv("REVIEW_SUBPROCESS_MAX_RUNTIME_SECONDS", "")).strip()
        if raw:
            try:
                return max(0, int(raw))
            except ValueError:
                return 0
        # 默认 2 小时兜底超时，防止子进程无限挂起占住任务位。
        return 7200

    def _terminate_process(self, process: mp.Process, *, review_id: str, reason: str) -> None:
        try:
            if not process.is_alive():
                return
            process.terminate()
            process.join(timeout=5.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=2.0)
            logger.warning(
                "review subprocess terminated review_id=%s pid=%s reason=%s",
                review_id,
                process.pid,
                reason,
            )
        except Exception as exc:
            logger.exception(
                "failed to terminate review subprocess review_id=%s pid=%s reason=%s error=%s",
                review_id,
                process.pid,
                reason,
                exc,
            )

    def _resolve_git_access_token(self, review_url: str, runtime_settings: RuntimeSettings) -> str:
        """按平台优先级选择最合适的代码平台 token。"""
        lowered = review_url.lower()
        if "github.com" in lowered:
            return str(runtime_settings.github_access_token or runtime_settings.code_repo_access_token or "").strip()
        if "gitlab" in lowered:
            return str(runtime_settings.gitlab_access_token or runtime_settings.code_repo_access_token or "").strip()
        if "codehub" in lowered:
            return str(runtime_settings.codehub_access_token or runtime_settings.code_repo_access_token or "").strip()
        return str(runtime_settings.code_repo_access_token or "").strip()

    def resolve_auto_review_repo_url(self, runtime: RuntimeSettings | None = None) -> str:
        """自动审核统一复用 code_repo_clone_url；旧字段仅作兼容回退。"""

        current = runtime or self.get_runtime_settings()
        return str(current.code_repo_clone_url or current.auto_review_repo_url or "").strip()

    def resolve_auto_review_repositories(self, runtime: RuntimeSettings | None = None, project_id: str = ""):
        """返回指定项目中启用自动审核的仓库列表；旧单仓仅作历史兜底。"""

        current = runtime or self.get_runtime_settings()
        repositories = current.auto_review_repositories(project_id)
        if repositories:
            return repositories
        repo_url = self.resolve_auto_review_repo_url(current)
        if not current.auto_review_enabled or not repo_url:
            return []
        repo = current.resolve_repository(repo_url=repo_url, project_id=project_id)
        return [repo] if repo is not None else []

    def _mark_failed(self, review_id: str, reason: str) -> ReviewTask:
        """统一把后台异常收口成 review 的 failed 状态。"""
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        review.status = "failed"
        review.phase = "failed"
        review.failure_reason = reason
        review.report_summary = f"审核失败：{reason}"
        if review.started_at is None:
            review.started_at = datetime.now(UTC)
        review.completed_at = datetime.now(UTC)
        review.duration_seconds = self._duration_seconds(review.started_at, review.completed_at)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        logger.error("review failed review_id=%s reason=%s", review_id, reason)
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="review_failed",
                phase="failed",
                message="代码审核任务执行失败",
                payload={"reason": reason},
            )
        )
        return review

    def _duration_seconds(self, started_at: datetime | None, completed_at: datetime | None) -> float | None:
        if started_at is None or completed_at is None:
            return None
        safe_started = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
        safe_completed = completed_at if completed_at.tzinfo is not None else completed_at.replace(tzinfo=UTC)
        return max(0.0, round((safe_completed - safe_started).total_seconds(), 3))

    def get_review(self, review_id: str) -> ReviewTask | None:
        review = self.review_repo.get(review_id)
        if review is None:
            return None
        return self._ensure_waiting_human_analysis_duration(review)

    def list_reviews(self) -> list[ReviewTask]:
        return [self._ensure_waiting_human_analysis_duration(review) for review in self.review_repo.list()]

    def list_review_summaries(self, project_id: str = "") -> list[dict[str, object]]:
        current_project_id = str(project_id or "").strip()
        rows = self.review_repo.list_light()
        if current_project_id:
            rows = [
                item
                for item in rows
                if str(item.get("project_id") or item.get("subject", {}).get("project_id") or "") == current_project_id
            ]
        rows = [self._ensure_waiting_human_summary_duration(item) for item in rows]
        rows = [self._sanitize_light_review_summary(item) for item in rows]
        return [self._apply_display_review_summary(item) for item in rows]

    def _sanitize_light_review_summary(self, review: dict[str, object]) -> dict[str, object]:
        """轻量历史列表也要避免暴露内部/旧口径文案。"""

        report_summary = str(review.get("report_summary") or "").strip()
        if not report_summary:
            return review
        next_review = dict(review)
        cleaned = report_summary
        cleaned = re.sub(r"形成\s*(\d+)\s*个议题", r"形成 \1 个正式问题", cleaned)
        cleaned = re.sub(r"其中\s*(\d+)\s*个待人工裁决", r"其中 \1 个待人工确认", cleaned)
        cleaned = cleaned.replace("findings", "检视发现")
        cleaned = cleaned.replace("finding", "检视发现")
        cleaned = cleaned.replace("人工裁决", "人工确认")
        cleaned = cleaned.replace("争议/裁决议题", "正式问题")
        cleaned = cleaned.replace("正式议题", "正式问题")
        cleaned = cleaned.replace("议题", "问题")
        cleaned = re.sub(r"条\s+检视发现", "条检视发现", cleaned)
        next_review["report_summary"] = cleaned
        return next_review

    def _apply_display_review_summary(self, review: dict[str, object]) -> dict[str, object]:
        review_id = str(review.get("review_id") or "").strip()
        if not review_id:
            return review
        if str(review.get("status") or "").lower() in {"failed", "closed", "cancelled"}:
            return review
        try:
            finding_count = len(self.list_display_findings(review_id))
            issue_count = len(self.list_display_issues(review_id))
        except Exception:
            return review
        next_review = dict(review)
        pending_human_count = len(list(next_review.get("pending_human_issue_ids") or []))
        next_review["finding_count"] = finding_count
        next_review["issue_count"] = issue_count
        if finding_count or issue_count or str(next_review.get("report_summary") or "").strip():
            next_review["report_summary"] = (
                f"审核报告已生成，共收敛 {finding_count} 条检视发现，"
                f"形成 {issue_count} 个有效问题，其中 {pending_human_count} 个待人工确认。"
            )
        return next_review

    def _ensure_waiting_human_analysis_duration(self, review: ReviewTask) -> ReviewTask:
        """历史 waiting_human 任务若缺少耗时，用进入人工门控的事件时间补齐。"""

        if (
            review.status != "waiting_human"
            or review.completed_at is not None
            or review.duration_seconds is not None
        ):
            return review
        human_gate_at = self._human_gate_requested_at(review.review_id)
        if human_gate_at is None:
            return review
        original_updated_at = review.updated_at
        review.completed_at = human_gate_at
        review.duration_seconds = self._duration_seconds(review.started_at or review.created_at, human_gate_at)
        review.updated_at = original_updated_at
        self.review_repo.save(review)
        return review

    def _ensure_waiting_human_summary_duration(self, review: dict[str, object]) -> dict[str, object]:
        if (
            review.get("status") != "waiting_human"
            or review.get("completed_at")
            or review.get("duration_seconds") is not None
        ):
            return review
        review_id = str(review.get("review_id") or "").strip()
        human_gate_at = self._human_gate_requested_at(review_id)
        if human_gate_at is None:
            return review
        started_raw = str(review.get("started_at") or review.get("created_at") or "").strip()
        try:
            started_at = datetime.fromisoformat(started_raw.replace("Z", "+00:00")) if started_raw else None
        except ValueError:
            started_at = None
        duration_seconds = self._duration_seconds(started_at, human_gate_at)
        next_review = dict(review)
        next_review["completed_at"] = human_gate_at.isoformat()
        next_review["duration_seconds"] = duration_seconds
        return next_review

    def _human_gate_requested_at(self, review_id: str) -> datetime | None:
        if not review_id:
            return None
        events = [
            event
            for event in self.event_repo.list(review_id)
            if event.event_type == "human_gate_requested" or event.phase == "human_gate"
        ]
        if not events:
            return None
        events.sort(key=lambda event: event.created_at)
        return events[0].created_at

    def save_benchmark_evaluation(self, review_id: str, evaluation: dict[str, object]) -> ReviewTask:
        """保存 Benchmark 人工评测结论到任务 metadata。"""

        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        expected_results = [
            {
                "text": str(item.get("text") or "").strip(),
                "verdict": str(item.get("verdict") or "pending").strip() or "pending",
                "matched_issue_ids": [
                    str(issue_id).strip()
                    for issue_id in item.get("matched_issue_ids", []) or []
                    if str(issue_id).strip()
                ],
                "comment": str(item.get("comment") or "").strip(),
            }
            for item in evaluation.get("expected_results", []) or []
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        benchmark_evaluation = {
            "expected_results": expected_results,
            "false_positive_issue_ids": [
                str(issue_id).strip()
                for issue_id in evaluation.get("false_positive_issue_ids", []) or []
                if str(issue_id).strip()
            ],
            "review_quality_score": int(evaluation.get("review_quality_score") or 0),
            "impact_quality_score": int(evaluation.get("impact_quality_score") or 0),
            "notes": str(evaluation.get("notes") or "").strip(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        review.subject.metadata = {
            **dict(review.subject.metadata or {}),
            "benchmark": True,
            "trigger_source": dict(review.subject.metadata or {}).get("trigger_source") or "benchmark_manual",
            "benchmark_evaluation": benchmark_evaluation,
        }
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        logger.info("benchmark evaluation saved review_id=%s expected_count=%s", review_id, len(expected_results))
        return review

    def list_pending_queue(self) -> list[ReviewTask]:
        """返回待处理队列（pending 状态）并按创建时间升序排列。"""

        queue = [item for item in self.review_repo.list() if item.status == "pending"]
        queue.sort(key=self._pending_sort_key)
        return queue

    def _pending_sort_key(self, review: ReviewTask) -> tuple[int, float]:
        """支持手动插队：被手动提升优先级的 pending 任务会排到最前面。"""

        metadata = dict(review.subject.metadata or {})
        priority_at = str(metadata.get("queue_priority_at") or "").strip()
        if priority_at:
            try:
                return (0, -datetime.fromisoformat(priority_at.replace("Z", "+00:00")).timestamp())
            except ValueError:
                return (0, -review.updated_at.timestamp())
        return (1, review.created_at.timestamp())

    def _pending_sort_key_from_payload(self, review: dict[str, object]) -> tuple[int, float]:
        metadata = {}
        subject = review.get("subject")
        if isinstance(subject, dict):
            raw_metadata = subject.get("metadata")
            if isinstance(raw_metadata, dict):
                metadata = dict(raw_metadata)
        priority_at = str(metadata.get("queue_priority_at") or "").strip()
        if priority_at:
            try:
                return (0, -datetime.fromisoformat(priority_at.replace("Z", "+00:00")).timestamp())
            except ValueError:
                return (0, -self._updated_at_or_created_at(review))
        return (1, self._created_at_timestamp(review))

    def _started_at_or_created_at(self, review: dict[str, object]) -> float:
        started_at = str(review.get("started_at") or "").strip()
        if started_at:
            try:
                return datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return self._created_at_timestamp(review)

    def _updated_at_or_created_at(self, review: dict[str, object]) -> float:
        updated_at = str(review.get("updated_at") or "").strip()
        if updated_at:
            try:
                return datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return self._created_at_timestamp(review)

    def _created_at_timestamp(self, review: dict[str, object]) -> float:
        created_at = str(review.get("created_at") or "").strip()
        if created_at:
            try:
                return datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return 0.0

    def list_pending_queue_with_diagnostics(self, project_id: str = "") -> list[dict[str, object]]:
        """返回待处理队列及每条任务当前未启动的原因说明。"""

        current_project_id = str(project_id or "").strip()
        reviews = self.review_repo.list()
        pending = [
            item
            for item in reviews
            if item.status == "pending"
            and (not current_project_id or str(item.subject.project_id or "") == current_project_id)
        ]
        pending.sort(key=self._pending_sort_key)
        running = sorted(
            [
                item
                for item in reviews
                if item.status == "running"
                and (not current_project_id or str(item.subject.project_id or "") == current_project_id)
            ],
            key=lambda item: item.started_at or item.created_at,
        )
        active_running = running[0] if running else None

        response: list[dict[str, object]] = []
        for index, item in enumerate(pending):
            blocker_code = "ready"
            blocker_message = "已满足启动条件，等待调度器拉起审核任务。"
            if active_running is not None and index == 0:
                blocker_code = "blocked_by_running_review"
                blocker_message = f"前序任务 {active_running.review_id} 正在执行，本任务会在它结束后自动启动。"
            elif active_running is not None:
                blocker_code = "waiting_for_turn_and_running_review"
                blocker_message = (
                    f"当前有任务 {active_running.review_id} 正在执行，且前方还有 {index} 条待处理任务，本任务需继续排队。"
                )
            elif index > 0:
                blocker_code = "waiting_for_turn"
                blocker_message = f"前方还有 {index} 条待处理任务，本任务会按顺序自动启动。"
            response.append(
                item.model_dump(mode="json")
                | {
                    "queue_position": index + 1,
                    "is_next_candidate": index == 0 and active_running is None,
                    "queue_blocker_code": blocker_code,
                    "queue_blocker_message": blocker_message,
                    "blocking_review_id": active_running.review_id if active_running is not None else "",
                }
            )
        return response

    def list_pending_queue_light_with_diagnostics(self, project_id: str = "") -> list[dict[str, object]]:
        """返回首页用轻量队列视图，避免加载完整 review subject。"""

        current_project_id = str(project_id or "").strip()
        reviews = self.review_repo.list_light()
        pending = [
            item
            for item in reviews
            if item.get("status") == "pending"
            and (not current_project_id or str(item.get("project_id") or "") == current_project_id)
        ]
        pending.sort(key=self._pending_sort_key_from_payload)
        running = sorted(
            [
                item
                for item in reviews
                if item.get("status") == "running"
                and (not current_project_id or str(item.get("project_id") or "") == current_project_id)
            ],
            key=self._started_at_or_created_at,
        )
        active_running = running[0] if running else None

        response: list[dict[str, object]] = []
        for index, item in enumerate(pending):
            blocker_code = "ready"
            blocker_message = "已满足启动条件，等待调度器拉起审核任务。"
            if active_running is not None and index == 0:
                blocker_code = "blocked_by_running_review"
                blocker_message = f"前序任务 {active_running.get('review_id', '')} 正在执行，本任务会在它结束后自动启动。"
            elif active_running is not None:
                blocker_code = "waiting_for_turn_and_running_review"
                blocker_message = (
                    f"当前有任务 {active_running.get('review_id', '')} 正在执行，且前方还有 {index} 条待处理任务，本任务需继续排队。"
                )
            elif index > 0:
                blocker_code = "waiting_for_turn"
                blocker_message = f"前方还有 {index} 条待处理任务，本任务会按顺序自动启动。"
            response.append(
                item
                | {
                    "queue_position": index + 1,
                    "is_next_candidate": index == 0 and active_running is None,
                    "queue_blocker_code": blocker_code,
                    "queue_blocker_message": blocker_message,
                    "blocking_review_id": active_running.get("review_id", "") if active_running is not None else "",
                }
            )
        return response

    def enqueue_open_merge_requests(self, repo_url: str, repository_id: str = "", project_id: str = "") -> list[ReviewTask]:
        """拉取仓库开放 MR/PR，并去重后加入待处理队列。"""

        runtime = self.get_runtime_settings()
        current_project_id = str(project_id or runtime.default_project_id or "").strip()
        repository = runtime.resolve_repository(repository_id=repository_id, repo_url=repo_url, project_id=current_project_id)
        effective_repo_url = str(repository.clone_url if repository is not None else repo_url).strip()
        token = self._resolve_git_access_token(effective_repo_url, runtime)
        merge_requests = self.platform_adapter.list_open_merge_requests(effective_repo_url, token, runtime)
        if not merge_requests:
            logger.info("auto queue scan returned no open merge requests repo_url=%s repository_id=%s", effective_repo_url, repository_id)
            return []

        existing_keys = self._existing_auto_queue_keys()
        created: list[ReviewTask] = []
        for item in merge_requests:
            queue_key = self._auto_queue_key(
                item,
                repository.repository_id if repository is not None else repository_id,
                current_project_id,
            )
            if queue_key in existing_keys:
                continue
            review = self.create_review(
                {
                    "subject_type": "mr",
                    "analysis_mode": runtime.default_analysis_mode,
                    "repo_id": repository.repository_id if repository is not None else repository_id,
                    "project_id": current_project_id,
                    "mr_url": item.mr_url,
                    "repo_url": effective_repo_url,
                    "source_ref": item.source_ref or "",
                    "target_ref": item.target_ref or (repository.default_branch if repository is not None else "") or runtime.default_target_branch or "main",
                    "title": item.title,
                    "metadata": {
                        "trigger_source": "auto_scheduler",
                        "project_id": current_project_id,
                        "auto_queue_key": queue_key,
                        "auto_queue_repo_url": effective_repo_url,
                        "repository_id": repository.repository_id if repository is not None else repository_id,
                        "repository_name": repository.name if repository is not None else "",
                        "auto_queue_mr_number": item.number,
                        "auto_queue_head_sha": item.head_sha,
                    },
                }
            )
            created.append(review)
            existing_keys.add(queue_key)

        if created:
            logger.info(
                "auto queue enqueued %s merge requests repo_url=%s review_ids=%s",
                len(created),
                effective_repo_url,
                [item.review_id for item in created],
            )
        return created

    def start_next_pending_review(self, project_id: str = "") -> ReviewTask | None:
        """在没有运行中任务时，按队列顺序启动下一条 pending 审核。"""

        recovered = self.recover_interrupted_reviews()
        if recovered:
            logger.warning(
                "auto queue recovered interrupted reviews review_ids=%s",
                [item.review_id for item in recovered],
            )
        reviews = self.review_repo.list()
        if any(item.status == "running" for item in reviews):
            return None
        current_project_id = str(project_id or "").strip()
        pending = [
            item
            for item in reviews
            if item.status == "pending"
            and (not current_project_id or str(item.subject.project_id or "") == current_project_id)
        ]
        if not pending:
            return None
        pending.sort(key=self._pending_sort_key)
        next_review = pending[0]
        logger.info("auto queue starting next review review_id=%s", next_review.review_id)
        return self.start_review_async(next_review.review_id)

    def queue_start_review(self, review_id: str) -> tuple[ReviewTask, str]:
        """手动启动队列任务；若当前已有运行中任务，则先插队并等待自动调度。"""

        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        if review.status == "closed":
            return review, "任务已关闭，不能再次启动。"
        if review.status == "running":
            return review, "任务已在运行中。"
        if review.status in {"completed", "failed", "waiting_human"}:
            return review, "当前任务不处于待启动状态。"

        metadata = dict(review.subject.metadata or {})
        metadata["queue_priority_at"] = datetime.now(UTC).isoformat()
        metadata["queue_priority_source"] = "manual_queue_start"
        review.subject.metadata = metadata
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)

        reviews = self.review_repo.list()
        running = next((item for item in reviews if item.status == "running" and item.review_id != review_id), None)
        if running is not None:
            logger.info(
                "review manually prioritized review_id=%s blocked_by=%s",
                review_id,
                running.review_id,
            )
            return review, f"任务已插队，等待运行中的任务 {running.review_id} 结束后自动启动。"
        started = self.start_review_async(review_id)
        return started, "任务已立即启动。"

    def close_review(self, review_id: str) -> ReviewTask:
        """关闭 pending/running/waiting_human 任务，并通知后台执行链尽快停止。"""

        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        if review.status == "closed":
            return review
        if review.status in {"completed", "failed"}:
            return review

        metadata = dict(review.subject.metadata or {})
        metadata["close_requested"] = True
        metadata["close_requested_at"] = datetime.now(UTC).isoformat()
        review.subject.metadata = metadata
        review.status = "closed"
        review.phase = "closed"
        review.failure_reason = ""
        review.report_summary = review.report_summary or "任务已由用户手动关闭。"
        review.completed_at = datetime.now(UTC)
        review.duration_seconds = self._duration_seconds(review.started_at or review.created_at, review.completed_at)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="review_closed",
                phase="closed",
                message="代码审核任务已被用户手动关闭",
            )
        )
        with self._active_review_processes_lock:
            process = self._active_review_processes.get(review_id)
        if process is not None:
            self._terminate_process(process, review_id=review_id, reason="closed_by_user")
        logger.warning("review closed by user review_id=%s", review.review_id)
        return review

    def rerun_failed_review(self, review_id: str) -> tuple[ReviewTask, str]:
        """重跑 failed/closed 任务，清理上一轮运行产物后重新入队或立即启动。"""

        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        if review.status not in {"failed", "closed"}:
            raise ValueError("only_failed_or_closed_review_can_rerun")
        source_status = review.status

        self._clear_review_runtime_outputs(review_id)

        metadata = dict(review.subject.metadata or {})
        for transient_key in (
            "close_requested",
            "close_requested_at",
            "queue_priority_at",
            "queue_priority_source",
        ):
            metadata.pop(transient_key, None)
        metadata["rerun_count"] = int(metadata.get("rerun_count") or 0) + 1
        metadata["last_rerun_at"] = datetime.now(UTC).isoformat()
        metadata["last_rerun_source"] = f"history_{source_status}_rerun"
        review.subject.metadata = metadata
        review.status = "pending"
        review.phase = "pending"
        review.failure_reason = ""
        review.report_summary = ""
        review.human_review_status = "not_required"
        review.pending_human_issue_ids = []
        review.started_at = None
        review.completed_at = None
        review.duration_seconds = None
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="review_rerun_requested",
                phase="pending",
                message="失败任务已清理旧运行数据，准备重新发起审核",
                payload={"rerun_count": metadata["rerun_count"]},
            )
        )
        logger.info(
            "review rerun requested review_id=%s from_status=%s rerun_count=%s",
            review.review_id,
            source_status,
            metadata["rerun_count"],
        )
        return self.queue_start_review(review.review_id)

    def delete_review(self, review_id: str) -> None:
        """删除单条已结束审核记录，并在后台安排一次 SQLite 压缩。"""

        self.delete_reviews([review_id])

    def delete_reviews(self, review_ids: list[str]) -> dict[str, object]:
        """批量删除已结束审核记录，并把 SQLite 压缩放到后台统一执行。"""

        normalized_ids = []
        seen_ids: set[str] = set()
        for review_id in review_ids:
            value = str(review_id or "").strip()
            if not value or value in seen_ids:
                continue
            normalized_ids.append(value)
            seen_ids.add(value)
        if not normalized_ids:
            return {"deleted_review_ids": [], "deleted_count": 0, "compaction_scheduled": False}

        reviews: list[ReviewTask] = []
        for review_id in normalized_ids:
            review = self.get_review(review_id)
            if review is None:
                raise KeyError(review_id)
            if review.status not in {"completed", "failed", "closed"}:
                raise ValueError("only_terminal_review_can_delete")
            reviews.append(review)

        for review in reviews:
            self._clear_review_runtime_outputs(review.review_id)
            shutil.rmtree(self.storage_root / "reviews" / review.review_id, ignore_errors=True)
            self.review_repo.delete(review.review_id)
            with self._active_reviews_lock:
                self._active_reviews.discard(review.review_id)
            logger.info("review deleted review_id=%s status=%s", review.review_id, review.status)

        compaction_scheduled = self._schedule_review_repo_compaction()
        return {
            "deleted_review_ids": [review.review_id for review in reviews],
            "deleted_count": len(reviews),
            "compaction_scheduled": compaction_scheduled,
        }

    def _schedule_review_repo_compaction(self) -> bool:
        """保证同一时间只触发一次 SQLite 压缩，避免删除接口长时间阻塞。"""

        if os.getenv("PYTEST_CURRENT_TEST"):
            self.review_repo.compact()
            return True
        if self._has_running_reviews():
            with self._db_compaction_lock:
                self._db_compaction_pending = True
            logger.info("sqlite compaction deferred because reviews are running")
            return True
        with self._db_compaction_lock:
            if self._db_compaction_running:
                self._db_compaction_pending = True
                return False
            self._db_compaction_running = True
            self._db_compaction_pending = False

        def _run_compaction() -> None:
            try:
                self.review_repo.compact()
                logger.info("sqlite compaction finished after review cleanup")
            except Exception as exc:
                logger.exception("sqlite compaction failed after review cleanup error=%s", exc)
            finally:
                with self._db_compaction_lock:
                    self._db_compaction_running = False

        threading.Thread(target=_run_compaction, daemon=True).start()
        return True

    def _has_running_reviews(self) -> bool:
        return any(str(item.get("status") or "").strip().lower() == "running" for item in self.review_repo.list_light())

    def _try_schedule_deferred_compaction(self) -> None:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        with self._db_compaction_lock:
            pending = self._db_compaction_pending
            running = self._db_compaction_running
        if not pending or running:
            return
        if self._has_running_reviews():
            return
        with self._db_compaction_lock:
            self._db_compaction_pending = False
        self._schedule_review_repo_compaction()

    def _clear_review_runtime_outputs(self, review_id: str) -> None:
        """清理某次审核上一轮运行产生的临时输出，避免重跑时混入旧结果。"""

        self.event_repo.delete_for_review(review_id)
        self.message_repo.delete_for_review(review_id)
        self.finding_repo.delete_for_review(review_id)
        self.issue_repo.delete_for_review(review_id)
        self.feedback_repo.delete_for_review(review_id)
        self.artifact_service.clear(review_id)
        self.runner.clear_runtime_caches()

    def recover_interrupted_reviews(self) -> list[ReviewTask]:
        """把异常退出后遗留的 running 任务恢复为 pending，避免阻塞自动队列。"""

        recovered: list[ReviewTask] = []
        with self._active_reviews_lock:
            active_ids = set(self._active_reviews)
        for review in self.review_repo.list():
            if review.status != "running":
                continue
            if review.review_id in active_ids:
                continue
            if not self._review_recovery_stale_window_elapsed(review):
                logger.info(
                    "skip recovering running review because it is still within stale window review_id=%s",
                    review.review_id,
                )
                continue
            if self._review_worker_process_alive(review):
                logger.info(
                    "skip recovering running review because worker process is still alive review_id=%s pid=%s",
                    review.review_id,
                    (review.subject.metadata or {}).get("worker_pid"),
                )
                continue
            review.status = "pending"
            review.phase = "pending"
            review.failure_reason = ""
            review.updated_at = datetime.now(UTC)
            self.review_repo.save(review)
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="review_recovered",
                    phase="pending",
                    message="检测到上次运行异常中断，任务已恢复到待处理队列",
                )
            )
            recovered.append(review)
        return recovered

    def _review_recovery_stale_window_elapsed(self, review: ReviewTask) -> bool:
        raw = str(os.getenv("REVIEW_RECOVERY_STALE_SECONDS", "")).strip()
        try:
            stale_seconds = max(0, int(raw)) if raw else 600
        except ValueError:
            stale_seconds = 600
        if stale_seconds <= 0:
            return True
        updated_at = review.updated_at
        if updated_at is None:
            return True
        safe_updated_at = updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - safe_updated_at).total_seconds() >= float(stale_seconds)

    def _review_worker_process_alive(self, review: ReviewTask) -> bool:
        """Best-effort guard so recovery does not duplicate an active subprocess review."""

        metadata = dict(review.subject.metadata or {})
        try:
            pid = int(metadata.get("worker_pid") or 0)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def list_events(self, review_id: str, *, since: str = "", limit: int = 0) -> list[ReviewEvent]:
        since_text = str(since or "").strip()
        if since_text:
            return self.event_repo.list_since(review_id, since=since_text, limit=(limit or 500))
        return self.event_repo.list(review_id)

    def list_findings(self, review_id: str, *, since: str = "", limit: int = 0) -> list[ReviewFinding]:
        since_text = str(since or "").strip()
        if since_text:
            return self.finding_repo.list_since(review_id, since=since_text, limit=(limit or 500))
        return self.finding_repo.list(review_id)

    def list_display_findings(self, review_id: str, *, since: str = "", limit: int = 0) -> list[ReviewFinding]:
        raw_findings = self.list_findings(review_id, since=since, limit=limit)
        if str(since or "").strip():
            return self._make_display_findings_texts_distinct([
                normalized
                for normalized in (self._normalize_display_report_finding(finding) for finding in raw_findings)
                if normalized is not None
            ])
        return self._make_display_findings_texts_distinct(
            self._ensure_display_findings_cover_issues(
                self._build_display_report_findings(raw_findings),
                self.list_issues(review_id),
            )
        )

    def get_finding(self, review_id: str, finding_id: str) -> ReviewFinding | None:
        finding = self.finding_repo.get(review_id, finding_id)
        if finding is None:
            synthetic = self._build_display_finding_for_synthetic_id(review_id, finding_id)
            return synthetic
        normalized = self._normalize_display_report_finding(finding)
        if normalized is not None:
            return normalized
        for issue in self.list_display_issues(review_id):
            linked_ids = {str(item or "").strip() for item in list(issue.finding_ids or []) if str(item or "").strip()}
            if str(issue.issue_id or "").strip() == str(finding_id or "").strip() or str(finding_id or "").strip() in linked_ids:
                return self._build_display_finding_from_issue(issue)
        return None

    def list_issues(self, review_id: str) -> list[DebateIssue]:
        issues = self.issue_repo.list(review_id)
        findings = self.finding_repo.list(review_id)
        finding_by_id = {item.finding_id: item for item in findings}
        if self._issues_require_finding_rehydration(issues, findings):
            issues = self._rehydrate_issues_from_findings(review_id, issues, findings)
            display_issues = [issue for issue in issues if _is_formal_issue(issue)]
            hydrated = self._hydrate_display_issues_from_findings(
                self._dedupe_display_issues(
                    self._add_missing_high_confidence_display_issues(review_id, display_issues, findings)
                ),
                findings,
            )
            return self._sync_pending_human_display_state(
                review_id,
                self._dedupe_display_issues(self._filter_user_visible_issues(hydrated)),
            )
        display_issues = [
            self._realign_issue_location(issue, finding_by_id)
            for issue in issues
            if _is_formal_issue(issue)
        ]
        hydrated = self._hydrate_display_issues_from_findings(
            self._dedupe_display_issues(
                self._add_missing_high_confidence_display_issues(review_id, display_issues, findings)
            ),
            findings,
        )
        return self._sync_pending_human_display_state(
            review_id,
            self._dedupe_display_issues(self._filter_user_visible_issues(hydrated)),
        )

    def list_display_issues(self, review_id: str) -> list[DebateIssue]:
        """返回面向前端展示的 issue 列表，统一清洗内部诊断文案。"""

        return self._make_display_issue_texts_distinct(
            [self._build_light_report_issue(issue) for issue in self.list_issues(review_id)]
        )

    def _filter_user_visible_issues(self, issues: list[DebateIssue]) -> list[DebateIssue]:
        """Hide historical/weak-model issues whose narrative does not match the anchor.

        Raw issues are still kept in storage for audit. This guard is only for the
        effective issue list shown to developers, where mismatched code anchors are
        more harmful than useful.
        """

        visible: list[DebateIssue] = []
        for issue in issues:
            normalized = self._normalize_report_issue_family(issue)
            family = self._display_issue_family(normalized)
            if self._display_issue_has_unresolved_consistency_failure(normalized, family):
                continue
            if family and not self._display_issue_anchor_valid(normalized, family):
                continue
            if self._display_current_and_suggested_code_are_same(normalized):
                continue
            normalized = self._repair_user_visible_consistency_failure(normalized, family)
            visible.append(self._normalize_user_visible_issue_state(normalized))
        return visible

    def _sync_pending_human_display_state(self, review_id: str, issues: list[DebateIssue]) -> list[DebateIssue]:
        review = self.get_review(review_id)
        if review is None:
            return issues
        pending_ids = {str(item or "").strip() for item in list(review.pending_human_issue_ids or []) if str(item or "").strip()}
        if not pending_ids:
            return issues
        synced: list[DebateIssue] = []
        for issue in issues:
            issue_ids = {
                str(issue.issue_id or "").strip(),
                str(issue.canonical_issue_id or "").strip(),
                *[str(item or "").strip() for item in list(issue.finding_ids or [])],
            }
            if issue_ids & pending_ids:
                synced.append(issue.model_copy(update={"status": "needs_human", "needs_human": True}))
                continue
            synced.append(issue)
        return synced

    @staticmethod
    def _repair_user_visible_consistency_failure(issue: DebateIssue, family: str) -> DebateIssue:
        status = str(issue.consistency_check_status or "").strip().lower()
        resolution = str(issue.resolution or "").strip().lower()
        if status != "downgraded" and resolution != "consistency_validation_failed":
            return issue
        return issue.model_copy(update={"status": "needs_human", "needs_human": True})

    @staticmethod
    def _normalize_user_visible_issue_state(issue: DebateIssue) -> DebateIssue:
        resolution = str(issue.resolution or "").strip().lower()
        consistency_status = str(issue.consistency_check_status or "").strip().lower()
        if resolution in {"accepted", "repaired"} or consistency_status == "repaired":
            return issue.model_copy(update={"status": "resolved", "needs_human": False, "needs_debate": False})
        return issue

    def _dedupe_display_issues(self, issues: list[DebateIssue]) -> list[DebateIssue]:
        """查询结果面向页面展示，再按归一化后的根因做一次轻量去重。"""

        deduped: dict[tuple[str, str, int], DebateIssue] = {}
        order: list[tuple[str, str, int]] = []
        exact_seen: dict[tuple[str, int, str, str], tuple[str, str, int]] = {}
        for issue in issues:
            normalized = self._normalize_report_issue_family(issue)
            family = self._display_issue_family(normalized)
            line_bucket = int(normalized.line_start or 1)
            if family in {"course_creation_semantics"}:
                line_bucket = 0
            key = (
                self._display_issue_path_key(normalized.file_path),
                family or str(normalized.normalized_issue_type or "").strip().lower(),
                line_bucket,
            )
            exact_key = self._exact_display_issue_key(normalized)
            if exact_key in exact_seen:
                key = exact_seen[exact_key]
            if key not in deduped:
                deduped[key] = normalized
                order.append(key)
                if exact_key:
                    exact_seen[exact_key] = key
                continue
            existing = deduped[key]
            merged_finding_ids = list(dict.fromkeys([*existing.finding_ids, *normalized.finding_ids]))
            merged_participants = list(dict.fromkeys([*existing.participant_expert_ids, *normalized.participant_expert_ids]))
            deduped[key] = existing.model_copy(
                update={
                    "finding_ids": merged_finding_ids,
                    "participant_expert_ids": merged_participants,
                    "confidence": max(float(existing.confidence or 0.0), float(normalized.confidence or 0.0)),
                    "evidence": list(dict.fromkeys([*existing.evidence, *normalized.evidence])),
                    "aggregated_titles": list(dict.fromkeys([*existing.aggregated_titles, normalized.title, *normalized.aggregated_titles]))[:10],
                    "aggregated_summaries": list(dict.fromkeys([*existing.aggregated_summaries, normalized.summary, *normalized.aggregated_summaries]))[:10],
                }
            )
        return self._make_display_issue_texts_distinct([deduped[key] for key in order])

    def _make_display_issue_texts_distinct(self, issues: list[DebateIssue]) -> list[DebateIssue]:
        summary_groups: dict[str, list[DebateIssue]] = {}
        title_groups: dict[str, list[DebateIssue]] = {}
        for issue in issues:
            summary_key = re.sub(r"\s+", "", str(issue.summary or "").strip().lower())
            if summary_key:
                summary_groups.setdefault(summary_key, []).append(issue)
            title_key = re.sub(r"\s+", "", str(issue.title or "").strip().lower())
            if title_key:
                title_groups.setdefault(title_key, []).append(issue)

        duplicate_summary_ids: set[str] = set()
        duplicate_title_ids: set[str] = set()
        use_path_label_by_id: dict[str, bool] = {}
        rewritten_by_id: dict[str, DebateIssue] = {}
        for groups, target in ((summary_groups, duplicate_summary_ids), (title_groups, duplicate_title_ids)):
            for duplicated in groups.values():
                anchors = {
                    (
                        str(issue.file_path or "").replace("\\", "/").strip().lower(),
                        int(issue.line_start or 1),
                    )
                    for issue in duplicated
                }
                if len(duplicated) <= 1 or len(anchors) <= 1:
                    continue
                target.update(str(issue.issue_id or "").strip() for issue in duplicated if str(issue.issue_id or "").strip())
                display_labels = [
                    self._build_display_anchor_label(issue, use_path=False)
                    for issue in duplicated
                ]
                if len(set(display_labels)) < len(display_labels):
                    for issue in duplicated:
                        issue_id = str(issue.issue_id or "").strip()
                        if issue_id:
                            use_path_label_by_id[issue_id] = True

        duplicated_ids = duplicate_summary_ids | duplicate_title_ids
        for issue in issues:
            issue_id = str(issue.issue_id or "").strip()
            if issue_id not in duplicated_ids:
                continue
            anchor_label = self._build_display_anchor_label(
                issue,
                use_path=bool(use_path_label_by_id.get(issue_id)),
            )
            summary = self._build_display_anchor_summary(issue, anchor_label=anchor_label) if issue_id in duplicate_summary_ids else issue.summary
            title = self._build_display_anchor_title(issue, anchor_label=anchor_label) if issue_id in duplicate_title_ids else issue.title
            rewritten_by_id[issue_id] = issue.model_copy(
                update={
                    "title": title,
                    "summary": summary,
                    "consistency_check_summary": (
                        f"{str(issue.consistency_check_summary or '').strip()} "
                        "系统已按文件和行号补充问题标题或摘要，避免不同位置展示成同一条问题。"
                    ).strip(),
                }
            )
        if not rewritten_by_id:
            return issues
        return [rewritten_by_id.get(issue.issue_id, issue) for issue in issues]

    @staticmethod
    def _display_issue_path_key(value: object) -> str:
        return str(value or "").replace("\\", "/").strip().lower()

    def _build_display_anchor_title(self, issue: DebateIssue, *, anchor_label: str = "") -> str:
        anchor = anchor_label or self._build_display_anchor_label(issue)
        title = self._sanitize_user_facing_issue_text(str(issue.title or "").strip())
        if title and not title.startswith(anchor):
            return f"{anchor}：{title}"
        return title or f"{anchor} 的代码问题"

    def _build_display_anchor_summary(self, issue: DebateIssue, *, anchor_label: str = "") -> str:
        code_hint = self._extract_display_code_hint(issue.current_code)
        anchor = anchor_label or self._build_display_anchor_label(issue)
        if code_hint:
            anchor = f"{anchor} 的 `{code_hint}`"
        summary = self._sanitize_user_facing_issue_text(str(issue.summary or "").strip())
        if summary and not summary.startswith(anchor):
            return f"{anchor}：{summary}"
        return summary or f"{anchor} 存在需要处理的代码风险。"

    @staticmethod
    def _display_issue_file_name(value: object) -> str:
        path = str(value or "").replace("\\", "/").rstrip("/")
        return path.split("/")[-1] or "当前文件"

    def _build_display_anchor_label(self, issue: DebateIssue, *, use_path: bool = False) -> str:
        line_text = f"第 {int(issue.line_start or 1)} 行"
        if not use_path:
            return f"{self._display_issue_file_name(issue.file_path)} {line_text}"
        path = str(issue.file_path or "").replace("\\", "/").strip("/")
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3:
            path = "/".join(parts[-3:])
        elif parts:
            path = "/".join(parts)
        else:
            path = "当前文件"
        return f"{path} {line_text}"

    @staticmethod
    def _extract_display_code_hint(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        for raw_line in text.splitlines():
            line = re.sub(r"^\s*\d+\s*\|\s*", "", str(raw_line or "").strip())
            line = re.sub(r"^[+\-]\s*", "", line).strip()
            if not line or line.startswith(("//", "/*", "*")):
                continue
            return f"{line[:93]}..." if len(line) > 96 else line
        return ""

    @staticmethod
    def _exact_display_issue_key(issue: DebateIssue) -> tuple[str, int, str, str] | None:
        title = re.sub(r"\s+", "", str(issue.title or "").strip().lower())
        summary = re.sub(r"\s+", "", str(issue.summary or "").strip().lower())
        if not title and not summary:
            return None
        return (
            str(issue.file_path or "").replace("\\", "/").strip().lower(),
            int(issue.line_start or 1),
            title[:120],
            summary[:180],
        )

    def _add_missing_high_confidence_display_issues(
        self,
        review_id: str,
        issues: list[DebateIssue],
        findings: list[ReviewFinding],
    ) -> list[DebateIssue]:
        """MiniMax 等弱模型有时在 debate 收敛时漏掉高置信直证据 finding。

        页面正式问题清单不能因此丢掉锁、异常、TODO、查询边界这类静态强证据问题。
        """

        existing_keys = {
            (str(issue.file_path or "").strip(), self._display_issue_family(issue))
            for issue in issues
        }
        candidates: dict[tuple[str, str], ReviewFinding] = {}
        for finding in findings:
            if self._display_finding_is_tool_observation_only(finding):
                continue
            issue = self._normalize_report_issue_family(self._build_issue_from_finding(review_id, finding, None))
            family = self._display_issue_family(issue)
            if not self._display_issue_anchor_valid(issue, family):
                continue
            if not self._display_issue_candidate_meets_threshold(finding):
                continue
            if family not in {
                "exception_swallowed",
                "comment_contract_unimplemented",
                "lock_guard_removed",
                "query_bound_removed",
                "query_boundary_missing",
                "n_plus_one",
                "sql_injection_risk",
                "code_injection_risk",
                "secret_leak_risk",
                "authorization_boundary_risk",
            }:
                continue
            key = (str(issue.file_path or "").strip(), family)
            if key in existing_keys:
                continue
            current = candidates.get(key)
            if current is None or float(finding.confidence or 0.0) > float(current.confidence or 0.0):
                candidates[key] = finding
        additions = [
            self._normalize_report_issue_family(self._build_issue_from_finding(review_id, finding, None))
            for finding in candidates.values()
        ]
        return [*issues, *additions]

    @staticmethod
    def _display_finding_is_tool_observation_only(finding: ReviewFinding) -> bool:
        code_context = dict(getattr(finding, "code_context", {}) or {})
        if bool(code_context.get("sast_fast_lane")):
            return True
        title = str(getattr(finding, "title", "") or "").strip()
        if not title.startswith("静态工具候选需复核"):
            return False
        evidence_source = str(code_context.get("evidence_source") or "").strip().lower()
        adopted_tool_observations = [
            str(item).strip()
            for item in list(code_context.get("adopted_tool_observations") or [])
            if str(item).strip()
        ]
        sast_prescan_matches = [
            item for item in list(code_context.get("sast_prescan_matches") or []) if isinstance(item, dict)
        ]
        normalized_issue_type = str(getattr(finding, "normalized_issue_type", "") or "").strip().lower()
        return bool(
            evidence_source in {"tool_observation", "sast_prescan"}
            or adopted_tool_observations
            or sast_prescan_matches
            or "tool_observation" in normalized_issue_type
        )

    def _display_issue_candidate_meets_threshold(self, finding: ReviewFinding) -> bool:
        """展示层补漏不能绕过设置页的 issue 升级阈值。"""

        try:
            runtime = self.get_runtime_settings()
        except Exception:
            runtime = RuntimeSettings()
        if not bool(getattr(runtime, "issue_filter_enabled", True)):
            return True
        priority = self._display_priority_for_severity(str(finding.severity or "medium"))
        min_priority = str(getattr(runtime, "issue_min_priority_level", "P2") or "P2").upper()
        if self._display_priority_rank(priority) > self._display_priority_rank(min_priority):
            return False
        threshold = self._display_confidence_threshold_for_priority(runtime, priority)
        return float(finding.confidence or 0.0) >= threshold

    @staticmethod
    def _display_priority_for_severity(severity: str) -> str:
        value = str(severity or "").strip().lower()
        if value == "blocker":
            return "P0"
        if value in {"critical", "high"}:
            return "P1"
        if value == "medium":
            return "P2"
        return "P3"

    @staticmethod
    def _display_priority_rank(priority: str) -> int:
        return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(str(priority or "").upper(), 2)

    @staticmethod
    def _display_confidence_threshold_for_priority(runtime: RuntimeSettings, priority: str) -> float:
        field = {
            "P0": "issue_confidence_threshold_p0",
            "P1": "issue_confidence_threshold_p1",
            "P2": "issue_confidence_threshold_p2",
            "P3": "issue_confidence_threshold_p3",
        }.get(str(priority or "").upper(), "issue_confidence_threshold_p2")
        try:
            return float(getattr(runtime, field, 0.8) or 0.8)
        except (TypeError, ValueError):
            return 0.8

    @staticmethod
    def _display_issue_anchor_valid(issue: DebateIssue, family: str) -> bool:
        current_code = str(issue.current_code or "").strip().lower()
        text = "\n".join(
            [
                issue.title,
                issue.summary,
                issue.current_code,
                issue.remediation_suggestion,
                *issue.evidence,
                *issue.aggregated_summaries,
            ]
        ).lower()
        if family == "exception_swallowed":
            has_exception_signal = any(
                token in text for token in ("catch", "runtimeexception", "exception", "ignored", "异常", "吞掉")
            )
            has_success_signal = any(token in text for token in ("success", "返回成功", "成功结果"))
            return has_exception_signal and has_success_signal
        if family == "comment_contract_unimplemented":
            if not current_code:
                return any(token in text for token in ("todo", "//", "/*", "承诺", "未实现", "应该", "需要"))
            return any(token in current_code for token in ("todo", "//", "/*", "承诺", "未实现", "应该", "需要"))
        if family == "lock_guard_removed":
            return any(token in text for token in ("synchronized", "lockregistry", "lockfor", "lock", "并发保护", "锁"))
        if family in {"query_bound_removed", "query_boundary_missing"}:
            if not current_code:
                return any(token in text for token in ("query", "search", "find", "pagerequest", "pageable", "分页", "limit"))
            return any(token in current_code for token in ("query", "search", "find", "pagerequest", "pageable", "分页", "limit"))
        if family == "event_consumer_batch_boundary":
            return "mysqldomaineventsconsumer" in str(issue.file_path or "").strip().lower() and any(
                token in text
                for token in (
                    "chunk",
                    "chunks",
                    "chunkstmp",
                    "setmaxresults",
                    "nativequery",
                    "createquery",
                    "query",
                    "limit",
                    "固定批次",
                    "事件消费",
                    "边界",
                )
            )
        if family == "n_plus_one":
            if not current_code:
                return any(token in text for token in ("for (", ".foreach", "while (", "循环", "逐条", "n+1", "repository.", ".save", "查库"))
            has_loop_context = any(token in current_code for token in ("for (", ".foreach", "while (", "循环", "逐条"))
            has_repeated_call = any(token in current_code for token in ("repository.", ".save", "gateway.", "find", "query", "查库"))
            if not has_loop_context and has_repeated_call:
                file_path = str(issue.file_path or "").strip().lower()
                if "coursecreator" not in file_path and any(
                    token in text for token in ("for (", ".foreach", "while (", "循环", "逐条", "n+1", "saveall")
                ):
                    return True
            return has_loop_context and has_repeated_call
        return True

    @classmethod
    def _display_current_and_suggested_code_are_same(cls, issue: DebateIssue) -> bool:
        current = cls._normalize_code_for_display_compare(issue.current_code)
        suggested = cls._normalize_code_for_display_compare(issue.suggested_code)
        return bool(current and suggested and current == suggested)

    @staticmethod
    def _display_issue_has_unresolved_consistency_failure(issue: DebateIssue, family: str) -> bool:
        status = str(issue.consistency_check_status or "").strip().lower()
        resolution = str(issue.resolution or "").strip().lower()
        if status != "downgraded" and resolution != "consistency_validation_failed":
            return False
        current_code = str(issue.current_code or "").strip().lower()
        if not current_code:
            return True
        if family == "comment_contract_unimplemented":
            return not any(token in current_code for token in ("todo", "//", "/*", "承诺", "应该", "需要"))
        if family == "n_plus_one":
            has_loop = any(token in current_code for token in ("for (", ".foreach", "while (", "循环", "逐条"))
            has_external_call = any(token in current_code for token in ("repository.", ".save", "gateway.", "find", "query", "查库"))
            return not (has_loop and has_external_call)
        if family == "lock_guard_removed":
            return not any(token in current_code for token in ("synchronized", "lockregistry", "lockfor", "lock", "锁"))
        if family == "exception_swallowed":
            return not any(token in current_code for token in ("catch", "runtimeexception", "exception", "ignored", "异常"))
        if family in {"query_bound_removed", "query_boundary_missing"}:
            return not any(token in current_code for token in ("query", "search", "find", "pagerequest", "pageable", "limit"))
        if family == "event_consumer_batch_boundary":
            text = "\n".join(
                [
                    current_code,
                    str(issue.title or "").strip().lower(),
                    str(issue.summary or "").strip().lower(),
                    str(issue.remediation_suggestion or "").strip().lower(),
                    str(issue.suggested_code or "").strip().lower(),
                ]
            )
            return not any(
                token in text
                for token in ("chunk", "chunks", "chunkstmp", "setmaxresults", "nativequery", "createquery", "limit", "固定批次", "事件消费")
            )
        return False

    @staticmethod
    def _normalize_code_for_display_compare(value: object) -> str:
        lines: list[str] = []
        for raw_line in str(value or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            line = re.sub(r"^\d+\s*\|\s?", "", line).strip()
            line = re.sub(r"^[+\-]\s?", "", line).strip()
            if line:
                lines.append(line)
        return re.sub(r"\s+", "", "\n".join(lines))

    @staticmethod
    def _display_issue_family(issue: DebateIssue) -> str:
        issue_type = str(issue.normalized_issue_type or "").strip().lower()
        text = "\n".join([issue.title, issue.summary, issue_type]).lower()
        compact = text.replace(" ", "")
        if issue_type in {"course_creation_semantics", "aggregate_factory_bypass", "aggregate_factory_bypassed"}:
            return "course_creation_semantics"
        if issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}:
            return "comment_contract_unimplemented"
        if issue_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}:
            return "lock_guard_removed"
        if issue_type in {"exception_swallowed", "exception_semantics_weakened"}:
            return "exception_swallowed"
        if issue_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}:
            return "query_bound_removed"
        if issue_type == "event_consumer_batch_boundary":
            return "event_consumer_batch_boundary"
        if issue_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}:
            return "n_plus_one"
        if issue_type in {"sql_injection_risk", "sql_injection", "command_injection_risk"}:
            return "sql_injection_risk"
        if issue_type in {"code_injection_risk", "eval_injection_risk", "xss_risk"}:
            return "code_injection_risk"
        if issue_type in {"secret_leak_risk", "sensitive_data_leak", "credential_leak_risk"}:
            return "secret_leak_risk"
        if issue_type in {"authorization_boundary_risk", "auth_bypass_risk", "access_control_risk"}:
            return "authorization_boundary_risk"
        if any(token in compact for token in ("承诺未落地", "todo", "扣减库存", "未实现")):
            return "comment_contract_unimplemented"
        if any(token in compact for token in ("锁", "并发保护", "synchronized", "lockregistry", "lockfor")):
            return "lock_guard_removed"
        if any(token in compact for token in ("query_bound", "unbounded", "pagerequest", "分页", "limit", "查询边界")):
            return "query_bound_removed"
        if any(token in compact for token in ("loop_call_amplification", "循环", "逐条", "n+1", "saveall", "repository.save")):
            return "n_plus_one"
        if any(token in compact for token in ("异常", "catch", "runtimeexception", "返回成功")):
            return "exception_swallowed"
        if any(token in compact for token in ("sql", "注入", "injection", "preparedstatement")):
            return "sql_injection_risk"
        if any(token in compact for token in ("token", "secret", "password", "credential", "凭证", "敏感")):
            return "secret_leak_risk"
        if any(token in compact for token in ("auth", "authorization", "鉴权", "权限", "越权")):
            return "authorization_boundary_risk"
        return issue_type

    def list_issue_messages(self, review_id: str, issue_id: str) -> list[ConversationMessage]:
        return self.message_repo.list_by_issue(review_id, issue_id)

    def list_all_messages(self, review_id: str, *, since: str = "", limit: int = 0) -> list[ConversationMessage]:
        since_text = str(since or "").strip()
        if since_text:
            return self.message_repo.list_since(review_id, since=since_text, limit=(limit or 500))
        return self.message_repo.list(review_id)

    def list_experts(self) -> list[ExpertProfile]:
        return self.expert_registry.list_all()

    def create_expert(self, payload: dict[str, object]) -> ExpertProfile:
        return self.expert_registry.create(payload)

    def update_expert(self, expert_id: str, payload: dict[str, object]) -> ExpertProfile:
        return self.expert_registry.update(expert_id, payload)

    def delete_expert(self, expert_id: str) -> None:
        self.expert_registry.delete(expert_id)

    def list_knowledge(self) -> list[KnowledgeDocument]:
        return self.knowledge_service.list_documents()

    def list_expert_knowledge(self, expert_id: str) -> list[KnowledgeDocument]:
        return self.knowledge_service.list_documents_for_expert(expert_id)

    def list_feedback_labels(self, review_id: str) -> list[FeedbackLabel]:
        return self.feedback_repo.list(review_id)

    def get_runtime_settings(self) -> RuntimeSettings:
        return self.runtime_settings_service.get()

    def update_runtime_settings(self, payload: dict[str, object]) -> RuntimeSettings:
        runtime = self.runtime_settings_service.update(payload)
        self._reload_storage_services()
        return runtime

    def list_extension_skills(self) -> list[ReviewSkillProfile]:
        return self.extension_editor_service.list_skills()

    def upsert_extension_skill(self, skill_id: str, payload: dict[str, object]) -> ReviewSkillProfile:
        return self.extension_editor_service.upsert_skill(skill_id, payload)

    def list_extension_tools(self) -> list[ReviewToolPlugin]:
        return self.extension_editor_service.list_tools()

    def upsert_extension_tool(self, tool_id: str, payload: dict[str, object]) -> ReviewToolPlugin:
        return self.extension_editor_service.upsert_tool(tool_id, payload)

    def read_extension_tool_script(self, tool_id: str, entry: str = "run.py") -> str:
        return self.extension_editor_service.read_tool_script(tool_id, entry)

    def get_change_impact_report_template(self) -> dict[str, object]:
        template_path = ChangeImpactReportService.TEMPLATE_PATH
        default_template_path = ChangeImpactReportService.DEFAULT_TEMPLATE_PATH
        schema_path = ChangeImpactReportService.SCHEMA_PATH
        default_schema_path = ChangeImpactReportService.DEFAULT_SCHEMA_PATH
        content = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
        schema_content = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""
        updated_at = ""
        schema_updated_at = ""
        if template_path.exists():
            updated_at = datetime.fromtimestamp(template_path.stat().st_mtime, tz=UTC).isoformat()
        if schema_path.exists():
            schema_updated_at = datetime.fromtimestamp(schema_path.stat().st_mtime, tz=UTC).isoformat()
        placeholders = ChangeImpactReportService.extract_template_placeholders(content)
        schema_payload = parse_json_object(schema_content)
        schema_variables = [
            item for item in list(schema_payload.get("variables") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        variable_names = {str(item.get("name") or "").strip() for item in schema_variables}
        return {
            "template_path": str(template_path),
            "default_template_path": str(default_template_path),
            "schema_path": str(schema_path),
            "default_schema_path": str(default_schema_path),
            "content": content,
            "schema_content": schema_content,
            "updated_at": updated_at,
            "schema_updated_at": schema_updated_at,
            "placeholders": placeholders,
            "schema_variables": schema_variables,
            "undefined_placeholders": [name for name in placeholders if name not in variable_names],
            "unused_variables": [name for name in variable_names if name not in placeholders],
        }

    def update_change_impact_report_template(self, content: str, schema_content: str = "") -> dict[str, object]:
        template_path = ChangeImpactReportService.TEMPLATE_PATH
        schema_path = ChangeImpactReportService.SCHEMA_PATH
        template_path.parent.mkdir(parents=True, exist_ok=True)
        normalized = str(content or "").rstrip() + "\n"
        template_path.write_text(normalized, encoding="utf-8")
        schema_payload = (
            parse_json_object(schema_content)
            if str(schema_content or "").strip()
            else ChangeImpactReportService().analyze_template_schema(normalized, self.get_runtime_settings())
        )
        normalized_schema = json.dumps(schema_payload, ensure_ascii=False, indent=2) + "\n"
        schema_path.write_text(normalized_schema, encoding="utf-8")
        return self.get_change_impact_report_template()

    def reset_change_impact_report_template(self) -> dict[str, object]:
        template_path = ChangeImpactReportService.TEMPLATE_PATH
        default_template_path = ChangeImpactReportService.DEFAULT_TEMPLATE_PATH
        schema_path = ChangeImpactReportService.SCHEMA_PATH
        default_schema_path = ChangeImpactReportService.DEFAULT_SCHEMA_PATH
        default_content = default_template_path.read_text(encoding="utf-8")
        default_schema_content = default_schema_path.read_text(encoding="utf-8")
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(default_content.rstrip() + "\n", encoding="utf-8")
        schema_path.write_text(default_schema_content.rstrip() + "\n", encoding="utf-8")
        return self.get_change_impact_report_template()

    def preview_change_impact_report_template(self, content: str, schema_content: str = "") -> dict[str, object]:
        template = str(content or "").strip() or self.get_change_impact_report_template().get("content") or ""
        schema_payload = (
            parse_json_object(schema_content)
            if str(schema_content or "").strip()
            else ChangeImpactReportService().analyze_template_schema(str(template), self.get_runtime_settings())
        )
        preview = ChangeImpactReportService().render_preview(str(template), schema_payload)
        return {
            **preview,
            "undefined_placeholders": [
                name
                for name in list(preview.get("placeholders") or [])
                if name not in {str(item.get("name") or "").strip() for item in list(preview.get("schema_variables") or []) if isinstance(item, dict)}
            ],
        }

    def analyze_change_impact_report_template(self, content: str) -> dict[str, object]:
        template = str(content or "").strip() or str(self.get_change_impact_report_template().get("content") or "")
        schema_payload = ChangeImpactReportService().analyze_template_schema(template, self.get_runtime_settings())
        placeholders = ChangeImpactReportService.extract_template_placeholders(template)
        variable_names = {
            str(item.get("name") or "").strip()
            for item in list(schema_payload.get("variables") or [])
            if isinstance(item, dict)
        }
        return {
            "schema_content": json.dumps(schema_payload, ensure_ascii=False, indent=2),
            "schema_variables": list(schema_payload.get("variables") or []),
            "placeholders": placeholders,
            "undefined_placeholders": [name for name in placeholders if name not in variable_names],
        }

    def build_repository_context_service(self, subject: dict[str, object] | None = None) -> RepositoryContextService:
        runtime = self.get_runtime_settings()
        return self.repository_resolver.build_context_service(runtime, subject)

    def get_artifacts(self, review_id: str) -> dict[str, object]:
        try:
            artifacts = self.artifact_service.load(review_id)
        except KeyError:
            return {}
        sanitized = self._sanitize_process_display_value(artifacts)
        return sanitized if isinstance(sanitized, dict) else {}












    def create_knowledge_document(self, payload: dict[str, object]) -> KnowledgeDocument:
        return self.knowledge_service.create_document(payload)

    def delete_knowledge_document(self, doc_id: str) -> bool:
        return self.knowledge_service.delete_document(doc_id)

    def retrieve_knowledge(
        self, expert_id: str, review_context: dict[str, object]
    ) -> list[KnowledgeDocument]:
        return self.knowledge_service.retrieve_for_expert(expert_id, review_context)
























    def _existing_auto_queue_keys(self) -> set[str]:
        keys: set[str] = set()
        for review in self.review_repo.list():
            metadata = review.subject.metadata or {}
            has_auto_key = False
            if isinstance(metadata, dict):
                auto_key = str(metadata.get("auto_queue_key") or "").strip()
                if auto_key:
                    keys.add(auto_key)
                    has_auto_key = True
            mr_url = str(review.subject.mr_url or "").strip()
            if mr_url and not has_auto_key:
                keys.add(f"url:{mr_url}")
        return keys

    def _auto_queue_key(self, merge_request: OpenMergeRequest, repository_id: str = "", project_id: str = "") -> str:
        project_prefix = f"project:{project_id}:" if project_id else ""
        repo_prefix = f"repo:{repository_id}:" if repository_id else ""
        prefix = f"{project_prefix}{repo_prefix}"
        if merge_request.head_sha:
            return f"{prefix}url:{merge_request.mr_url}#sha:{merge_request.head_sha}"
        return f"{prefix}url:{merge_request.mr_url}"

    def record_human_decision(
        self, review_id: str, issue_id: str, decision: str, comment: str
    ) -> ReviewTask:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        issues = self.issue_repo.list(review_id)
        target_issue = next((item for item in issues if item.issue_id == issue_id), None)
        if target_issue is None:
            raise KeyError(issue_id)
        if not target_issue.needs_human or target_issue.status == "resolved":
            raise ValueError("issue is not pending human decision")
        updated_issues: list[DebateIssue] = []
        touched = False
        for issue in issues:
            if issue.issue_id != issue_id:
                updated_issues.append(issue)
                continue
            touched = True
            updated_issues.append(
                issue.model_copy(
                    update={
                        "human_decision": decision,
                        "status": "resolved",
                        "resolution": f"human_{decision}",
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
        if not touched:
            raise KeyError(issue_id)
        self.issue_repo.save_all(review_id, updated_issues)
        feedback_label = "accepted_risk" if decision == "approved" else "false_positive"
        self.feedback_repo.save(
            FeedbackLabel(
                review_id=review_id,
                issue_id=issue_id,
                label=feedback_label,
                comment=comment,
            )
        )
        if decision in {"approved", "rejected"} and self.review_learning_service is not None:
            refreshed_issue = next((item for item in updated_issues if item.issue_id == issue_id), target_issue)
            self.review_learning_service.record_issue_decision_case(
                review=review,
                issue=refreshed_issue,
                decision=decision,
                comment=comment,
            )
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id=issue_id,
                expert_id="human_reviewer",
                message_type="human_comment",
                content=comment,
                metadata={"decision": decision},
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="human_decision_recorded",
                phase="human_gate",
                message=f"人工审核已{decision}",
                payload={"issue_id": issue_id, "decision": decision},
            )
        )
        pending_ids = [item.issue_id for item in updated_issues if item.needs_human and item.status != "resolved"]
        review.status = "completed" if not pending_ids else "waiting_human"
        review.phase = "completed" if not pending_ids else "human_gate"
        review.human_review_status = decision if not pending_ids else "requested"
        review.pending_human_issue_ids = pending_ids
        if not pending_ids:
            review.completed_at = review.completed_at or datetime.now(UTC)
            review.duration_seconds = self._duration_seconds(review.started_at or review.created_at, review.completed_at)
        formal_issues = [issue for issue in updated_issues if _is_formal_issue(issue)]
        review.report_summary = build_report_summary(
            review=review,
            finding_count=len(self.list_findings(review_id)),
            issue_count=len(formal_issues),
            pending_human_count=len(pending_ids),
        )
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.artifact_service.publish(review, formal_issues)
        if not pending_ids:
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="review_completed",
                    phase="completed",
                    message="人工裁决后审核完成",
                )
            )
        return review

    def list_review_learning_cases(self, *, repo_id: str = "", issue_type: str = "") -> list[dict[str, object]]:
        if self.review_learning_service is None:
            return []
        return self.review_learning_service.list_cases(repo_id=repo_id, issue_type=issue_type)

    def update_review_learning_case_status(self, case_id: str, *, status: str) -> dict[str, object]:
        if self.review_learning_service is None:
            raise KeyError(case_id)
        return self.review_learning_service.update_case_status(case_id, status=status)

    def record_impact_feedback(
        self,
        review_id: str,
        target_type: str,
        target_key: str,
        label: str,
        comment: str = "",
    ) -> FeedbackLabel:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        normalized_type = str(target_type or "").strip().lower().replace(" ", "_")
        normalized_key = str(target_key or "").strip()
        normalized_label = str(label or "").strip().lower()
        if not normalized_type or not normalized_key:
            raise ValueError("target_type and target_key are required")
        label_mapping = {
            "confirmed": "impact_confirmed",
            "true_positive": "impact_confirmed",
            "impact_confirmed": "impact_confirmed",
            "false_positive": "impact_false_positive",
            "rejected": "impact_false_positive",
            "impact_false_positive": "impact_false_positive",
        }
        feedback_label = label_mapping.get(normalized_label)
        if feedback_label is None:
            raise ValueError("unsupported impact feedback label")

        digest = hashlib.sha1(f"{normalized_type}:{normalized_key}".encode("utf-8")).hexdigest()[:12]
        metadata = {
            "target_type": normalized_type,
            "target_key": normalized_key,
            "comment": comment,
        }
        saved = self.feedback_repo.save(
            FeedbackLabel(
                review_id=review_id,
                issue_id=f"impact:{normalized_type}:{digest}",
                label=feedback_label,
                source="impact_feedback",
                comment=json.dumps(metadata, ensure_ascii=False),
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="impact_feedback_recorded",
                phase=review.phase,
                message="关联影响反馈已记录",
                payload={
                    "target_type": normalized_type,
                    "target_key": normalized_key,
                    "label": feedback_label,
                },
            )
        )
        return saved


review_service = ReviewService()
