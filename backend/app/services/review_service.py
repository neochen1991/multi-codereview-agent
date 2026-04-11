from __future__ import annotations

import json
import threading
import os
import multiprocessing as mp
import logging
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
from app.domain.models.report import ReviewReport
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.review_skill import ReviewSkillProfile
from app.domain.models.review_tool_plugin import ReviewToolPlugin
from app.domain.models.runtime_settings import RuntimeSettings
from app.logging_config import configure_logging as configure_app_logging
from app.repositories.sqlite_event_repository import SqliteEventRepository
from app.repositories.sqlite_feedback_repository import SqliteFeedbackRepository
from app.repositories.sqlite_finding_repository import SqliteFindingRepository
from app.repositories.sqlite_issue_repository import SqliteIssueRepository
from app.repositories.sqlite_message_repository import SqliteMessageRepository
from app.repositories.sqlite_review_repository import SqliteReviewRepository
from app.services.artifact_service import ArtifactService, build_report_summary
from app.services.expert_registry import ExpertRegistry
from app.services.extension_editor_service import ExtensionEditorService
from app.services.feedback_learner_service import FeedbackLearnerService
from app.services.knowledge_service import KnowledgeService
from app.services.platform_adapter import OpenMergeRequest, PlatformAdapter
from app.services.repository_context_service import RepositoryContextService
from app.services.review_runner import ReviewClosedError, ReviewRunner
from app.services.runtime_settings_service import RuntimeSettingsService

logger = logging.getLogger(__name__)


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


class ReviewService:
    """审核应用层总入口。

    这层面向 API 和页面交互，负责把“创建任务、启动执行、查询结果、管理专家/知识库”
    这些应用动作统一收口。真正的专家分析和裁决由 ReviewRunner 完成，
    这里更像一个把配置、平台适配、仓储和运行时串起来的协调层。
    """

    def __init__(self, storage_root: Path | None = None) -> None:
        self.storage_root = Path(storage_root or settings.STORAGE_ROOT)
        db_path = self._resolve_db_path(self.storage_root)
        self.review_repo = SqliteReviewRepository(db_path)
        self.event_repo = SqliteEventRepository(db_path)
        self.feedback_repo = SqliteFeedbackRepository(db_path)
        self.finding_repo = SqliteFindingRepository(db_path)
        self.issue_repo = SqliteIssueRepository(db_path)
        self.message_repo = SqliteMessageRepository(db_path)
        self.runner = ReviewRunner(self.storage_root)
        self.artifact_service = ArtifactService(self.storage_root)
        self.expert_registry = ExpertRegistry(self.storage_root / "experts")
        self.feedback_learner_service = FeedbackLearnerService(self.storage_root)
        self.knowledge_service = KnowledgeService(self.storage_root)
        self.knowledge_service.bootstrap_builtin_documents()
        self.runtime_settings_service = RuntimeSettingsService(self.storage_root)
        self.platform_adapter = PlatformAdapter()
        self.extension_editor_service = ExtensionEditorService(Path(__file__).resolve().parents[3])
        self._active_reviews: set[str] = set()
        self._active_reviews_lock = threading.Lock()
        self._active_review_processes: dict[str, mp.Process] = {}
        self._active_review_processes_lock = threading.Lock()
        self._db_compaction_running = False
        self._db_compaction_lock = threading.Lock()

    def _resolve_db_path(self, root: Path) -> Path:
        """Resolve SQLite path from storage root, honoring global default when unchanged."""

        resolved_root = Path(root).resolve()
        default_storage_root = Path(settings.STORAGE_ROOT).resolve()
        if resolved_root == default_storage_root:
            return Path(settings.SQLITE_DB_PATH)
        return resolved_root / "app.db"

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
        selected_experts = [
            str(expert_id).strip()
            for expert_id in payload.pop("selected_experts", []) or []
            if str(expert_id).strip()
        ]
        design_docs = [
            item
            for item in payload.pop("design_docs", []) or []
            if isinstance(item, dict) and str(item.get("content") or "").strip()
        ]
        if not str(payload.get("access_token") or "").strip():
            review_url = str(payload.get("mr_url") or payload.get("repo_url") or "")
            configured_token = self._resolve_git_access_token(review_url, runtime_settings)
            if configured_token:
                payload["access_token"] = configured_token
        subject = self.platform_adapter.normalize(ReviewSubject.model_validate(payload), runtime_settings)
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
        return max(0.0, round((completed_at - started_at).total_seconds(), 3))

    def get_review(self, review_id: str) -> ReviewTask | None:
        return self.review_repo.get(review_id)

    def list_reviews(self) -> list[ReviewTask]:
        return self.review_repo.list()

    def list_review_summaries(self) -> list[dict[str, object]]:
        return self.review_repo.list_light()

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

    def list_pending_queue_with_diagnostics(self) -> list[dict[str, object]]:
        """返回待处理队列及每条任务当前未启动的原因说明。"""

        reviews = self.review_repo.list()
        pending = [item for item in reviews if item.status == "pending"]
        pending.sort(key=self._pending_sort_key)
        running = sorted(
            [item for item in reviews if item.status == "running"],
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

    def list_pending_queue_light_with_diagnostics(self) -> list[dict[str, object]]:
        """返回首页用轻量队列视图，避免加载完整 review subject。"""

        reviews = self.review_repo.list_light()
        pending = [item for item in reviews if item.get("status") == "pending"]
        pending.sort(key=self._pending_sort_key_from_payload)
        running = sorted(
            [item for item in reviews if item.get("status") == "running"],
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

    def enqueue_open_merge_requests(self, repo_url: str) -> list[ReviewTask]:
        """拉取仓库开放 MR/PR，并去重后加入待处理队列。"""

        runtime = self.get_runtime_settings()
        token = self._resolve_git_access_token(repo_url, runtime)
        merge_requests = self.platform_adapter.list_open_merge_requests(repo_url, token, runtime)
        if not merge_requests:
            logger.info("auto queue scan returned no open merge requests repo_url=%s", repo_url)
            return []

        existing_keys = self._existing_auto_queue_keys()
        created: list[ReviewTask] = []
        for item in merge_requests:
            queue_key = self._auto_queue_key(item)
            if queue_key in existing_keys:
                continue
            review = self.create_review(
                {
                    "subject_type": "mr",
                    "analysis_mode": runtime.default_analysis_mode,
                    "repo_id": "",
                    "project_id": "",
                    "mr_url": item.mr_url,
                    "source_ref": item.source_ref or "",
                    "target_ref": item.target_ref or runtime.default_target_branch or "main",
                    "title": item.title,
                    "metadata": {
                        "trigger_source": "auto_scheduler",
                        "auto_queue_key": queue_key,
                        "auto_queue_repo_url": repo_url,
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
                repo_url,
                [item.review_id for item in created],
            )
        return created

    def start_next_pending_review(self) -> ReviewTask | None:
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
        pending = [item for item in reviews if item.status == "pending"]
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
        with self._db_compaction_lock:
            if self._db_compaction_running:
                return False
            self._db_compaction_running = True

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

    def list_events(self, review_id: str) -> list[ReviewEvent]:
        return self.event_repo.list(review_id)

    def list_findings(self, review_id: str) -> list[ReviewFinding]:
        return self.finding_repo.list(review_id)

    def get_finding(self, review_id: str, finding_id: str) -> ReviewFinding | None:
        return self.finding_repo.get(review_id, finding_id)

    def list_issues(self, review_id: str) -> list[DebateIssue]:
        return self.issue_repo.list(review_id)

    def list_issue_messages(self, review_id: str, issue_id: str) -> list[ConversationMessage]:
        return self.message_repo.list_by_issue(review_id, issue_id)

    def list_all_messages(self, review_id: str) -> list[ConversationMessage]:
        return self.message_repo.list(review_id)

    def list_experts(self) -> list[ExpertProfile]:
        return self.expert_registry.list_all()

    def create_expert(self, payload: dict[str, object]) -> ExpertProfile:
        return self.expert_registry.create(payload)

    def update_expert(self, expert_id: str, payload: dict[str, object]) -> ExpertProfile:
        return self.expert_registry.update(expert_id, payload)

    def list_knowledge(self) -> list[KnowledgeDocument]:
        return self.knowledge_service.list_documents()

    def list_expert_knowledge(self, expert_id: str) -> list[KnowledgeDocument]:
        return self.knowledge_service.list_documents_for_expert(expert_id)

    def list_feedback_labels(self, review_id: str) -> list[FeedbackLabel]:
        return self.feedback_repo.list(review_id)

    def get_runtime_settings(self) -> RuntimeSettings:
        return self.runtime_settings_service.get()

    def update_runtime_settings(self, payload: dict[str, object]) -> RuntimeSettings:
        return self.runtime_settings_service.update(payload)

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

    def build_repository_context_service(self, subject: dict[str, object] | None = None) -> RepositoryContextService:
        runtime = self.get_runtime_settings()
        return RepositoryContextService.from_review_context(
            clone_url=runtime.code_repo_clone_url,
            local_path=runtime.code_repo_local_path,
            default_branch=runtime.code_repo_default_branch or runtime.default_target_branch,
            access_token=runtime.code_repo_access_token,
            auto_sync=runtime.code_repo_auto_sync,
            subject=subject,
        )

    def get_artifacts(self, review_id: str) -> dict[str, object]:
        try:
            return self.artifact_service.load(review_id)
        except KeyError:
            return {}

    def build_quality_metrics(self) -> dict[str, float | int]:
        reviews = self.list_reviews()
        total_issues = 0
        tool_verified = 0
        debated = 0
        surviving = 0
        needs_human = 0
        false_positive = 0
        for review in reviews:
            issues = self.list_issues(review.review_id)
            feedback_labels = self.list_feedback_labels(review.review_id)
            total_issues += len(issues)
            tool_verified += len([item for item in issues if item.tool_verified])
            debated += len([item for item in issues if item.needs_debate])
            surviving += len(
                [
                    item
                    for item in issues
                    if item.needs_debate and item.resolution in {"judge_accepted", "human_approved"}
                ]
            )
            needs_human += len([item for item in issues if item.needs_human])
            false_positive += len([item for item in feedback_labels if item.label == "false_positive"])
        denominator = total_issues or 1
        debated_denominator = debated or 1
        return {
            "review_count": len(reviews),
            "issue_count": total_issues,
            "tool_confirmation_rate": round(tool_verified / denominator, 2),
            "debate_survival_rate": round(surviving / debated_denominator, 2),
            "needs_human_count": needs_human,
            "false_positive_count": false_positive,
        }

    def build_expert_metrics(self) -> list[dict[str, object]]:
        return self.feedback_learner_service.build_expert_metrics()

    def build_llm_timeout_metrics(self, *, tail_lines: int = 4000) -> dict[str, object]:
        """从后端日志中聚合最近一段时间的 LLM timeout 与耗时概览。"""

        log_path = settings.LOGS_ROOT / "backend.log"
        empty_payload = {
            "timeout_count": 0,
            "connect_timeout_count": 0,
            "read_timeout_count": 0,
            "write_timeout_count": 0,
            "pool_timeout_count": 0,
            "other_timeout_count": 0,
            "success_count": 0,
            "avg_success_elapsed_ms": 0.0,
            "max_success_elapsed_ms": 0.0,
            "recent_timeouts": [],
        }
        if not log_path.exists():
            return empty_payload
        try:
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-max(100, tail_lines) :]
        except Exception:
            return empty_payload

        timeout_counters = {
            "connect_timeout": 0,
            "read_timeout": 0,
            "write_timeout": 0,
            "pool_timeout": 0,
            "timeout": 0,
        }
        recent_timeouts: list[dict[str, object]] = []
        success_elapsed: list[float] = []
        for line in lines:
            if "llm request timeout " in line:
                timeout_kind = self._extract_log_field(line, "timeout_kind") or "timeout"
                counter_key = timeout_kind if timeout_kind in timeout_counters else "timeout"
                timeout_counters[counter_key] += 1
                recent_timeouts.append(
                    {
                        "timestamp": self._extract_log_timestamp(line),
                        "timeout_kind": timeout_kind,
                        "provider": self._extract_log_field(line, "provider"),
                        "model": self._extract_log_field(line, "model"),
                        "phase": self._extract_context_field(line, "phase"),
                        "review_id": self._extract_context_field(line, "review_id"),
                        "expert_id": self._extract_context_field(line, "expert_id")
                        or self._extract_context_field(line, "agent_id"),
                        "attempt_elapsed_ms": self._extract_float_log_field(line, "attempt_elapsed_ms"),
                        "total_elapsed_ms": self._extract_float_log_field(line, "total_elapsed_ms"),
                    }
                )
            elif "llm response parsed " in line:
                elapsed = self._extract_float_log_field(line, "total_elapsed_ms")
                if elapsed > 0:
                    success_elapsed.append(elapsed)

        timeout_count = sum(timeout_counters.values())
        avg_success = round(sum(success_elapsed) / len(success_elapsed), 2) if success_elapsed else 0.0
        max_success = round(max(success_elapsed), 2) if success_elapsed else 0.0
        return {
            "timeout_count": timeout_count,
            "connect_timeout_count": timeout_counters["connect_timeout"],
            "read_timeout_count": timeout_counters["read_timeout"],
            "write_timeout_count": timeout_counters["write_timeout"],
            "pool_timeout_count": timeout_counters["pool_timeout"],
            "other_timeout_count": timeout_counters["timeout"],
            "success_count": len(success_elapsed),
            "avg_success_elapsed_ms": avg_success,
            "max_success_elapsed_ms": max_success,
            "recent_timeouts": recent_timeouts[-10:],
        }

    def _extract_log_timestamp(self, line: str) -> str:
        match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})", line)
        return match.group(1) if match else ""

    def _extract_log_field(self, line: str, field: str) -> str:
        match = re.search(rf"{re.escape(field)}=([^\s]+)", line)
        return match.group(1).strip().strip(",") if match else ""

    def _extract_float_log_field(self, line: str, field: str) -> float:
        raw = self._extract_log_field(line, field)
        try:
            return float(raw)
        except Exception:
            return 0.0

    def _safe_int(self, value: object) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _extract_context_field(self, line: str, field: str) -> str:
        match = re.search(r"context=(\{.*?\})(?:\s+\w+=|$)", line)
        if not match:
            return ""
        try:
            payload = json.loads(match.group(1))
        except Exception:
            return ""
        value = payload.get(field)
        return str(value).strip() if value is not None else ""

    def create_knowledge_document(self, payload: dict[str, object]) -> KnowledgeDocument:
        return self.knowledge_service.create_document(payload)

    def delete_knowledge_document(self, doc_id: str) -> bool:
        return self.knowledge_service.delete_document(doc_id)

    def retrieve_knowledge(
        self, expert_id: str, review_context: dict[str, object]
    ) -> list[KnowledgeDocument]:
        return self.knowledge_service.retrieve_for_expert(expert_id, review_context)

    def build_report(self, review_id: str) -> ReviewReport:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        findings = self.list_findings(review_id)
        light_findings = [self._build_light_report_finding(item) for item in findings]
        issues = self.list_issues(review_id)
        issue_filter_decisions = self._build_issue_filter_decisions(review_id)
        issue_count = len(issues)
        summary = (
            f"本次代码审核共收敛 {len(findings)} 条发现，"
            f"形成 {len(issues)} 个争议/裁决议题，"
            f"覆盖 {len(review.selected_experts)} 个专家视角，"
            f"当前状态为 {review.status}。"
        )
        return ReviewReport(
            review_id=review_id,
            status=review.status,
            phase=review.phase,
            summary=summary,
            review=ReviewTask.model_validate(self._build_light_review_payload(review)),
            findings=light_findings,
            issues=issues,
            issue_count=issue_count,
            human_review_status=review.human_review_status,
            llm_usage_summary=self.message_repo.summarize_llm_usage(review_id),
            issue_filter_decisions=issue_filter_decisions,
            confidence_summary={
                "high_confidence_count": len(
                    [item for item in findings if item.confidence >= 0.85]
                ),
                "debated_issue_count": len(
                    [item for item in issues if item.status in {"debating", "needs_human", "resolved"}]
                ),
                "needs_human_count": len([item for item in issues if item.needs_human]),
                "verified_issue_count": len([item for item in issues if item.verified]),
                "direct_defect_count": len([item for item in findings if item.finding_type == "direct_defect"]),
                "risk_hypothesis_count": len([item for item in findings if item.finding_type == "risk_hypothesis"]),
                "test_gap_count": len([item for item in findings if item.finding_type == "test_gap"]),
                "design_concern_count": len([item for item in findings if item.finding_type == "design_concern"]),
            },
        )

    def _build_light_report_finding(self, finding: ReviewFinding) -> ReviewFinding:
        """结果页首屏只返回轻量 finding，避免 report 载荷过大。"""

        payload = finding.model_dump(mode="json")
        payload["evidence"] = list(payload.get("evidence") or [])[:4]
        payload["cross_file_evidence"] = []
        payload["assumptions"] = list(payload.get("assumptions") or [])[:4]
        payload["context_files"] = list(payload.get("context_files") or [])[:8]
        payload["matched_rules"] = list(payload.get("matched_rules") or [])[:8]
        payload["violated_guidelines"] = list(payload.get("violated_guidelines") or [])[:8]
        payload["remediation_steps"] = list(payload.get("remediation_steps") or [])[:6]
        payload["code_excerpt"] = ""
        payload["code_context"] = {}
        payload["suggested_code"] = ""
        return ReviewFinding.model_validate(payload)

    def _build_issue_filter_decisions(self, review_id: str) -> list[dict[str, object]]:
        """提炼结果页需要的阈值过滤决策，避免结果页拉取全量消息。"""

        decisions: list[dict[str, object]] = []
        for message in self.list_all_messages(review_id):
            if message.message_type != "issue_filter_applied":
                continue
            raw = message.metadata.get("issue_filter_decisions")
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                decisions.append(
                    {
                        "topic": str(item.get("topic") or ""),
                        "rule_code": str(item.get("rule_code") or ""),
                        "rule_label": str(item.get("rule_label") or ""),
                        "reason": str(item.get("reason") or ""),
                        "severity": str(item.get("severity") or ""),
                        "finding_ids": [str(entry) for entry in (item.get("finding_ids") or []) if str(entry).strip()],
                        "finding_titles": [
                            str(entry) for entry in (item.get("finding_titles") or []) if str(entry).strip()
                        ],
                        "expert_ids": [str(entry) for entry in (item.get("expert_ids") or []) if str(entry).strip()],
                    }
                )
        return decisions

    def build_review_snapshot(self, review_id: str) -> dict[str, object]:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        return self._build_light_review_payload(review)

    def build_replay_bundle(self, review_id: str) -> dict[str, object]:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        return {
            "review": self._build_light_review_payload(review),
            "events": [item.model_dump(mode="json") for item in self.list_events(review_id)],
            "messages": [self._build_replay_message(item) for item in self.list_all_messages(review_id)],
        }

    def build_process_messages(self, review_id: str) -> list[dict[str, object]]:
        return [self._build_process_message(item) for item in self.list_all_messages(review_id)]

    def _build_light_review_payload(self, review: ReviewTask) -> dict[str, object]:
        payload = review.model_dump(mode="json")
        subject = payload.get("subject")
        if isinstance(subject, dict):
            subject["unified_diff"] = ""
            metadata = subject.get("metadata")
            if isinstance(metadata, dict):
                subject["metadata"] = self._build_light_subject_metadata(metadata)
        return payload

    def _build_light_subject_metadata(self, metadata: dict[str, object]) -> dict[str, object]:
        payload = dict(metadata or {})
        design_docs = payload.get("design_docs")
        if isinstance(design_docs, list):
            payload["design_docs"] = [
                {
                    "doc_id": item.get("doc_id"),
                    "title": item.get("title"),
                    "filename": item.get("filename"),
                    "doc_type": item.get("doc_type"),
                }
                for item in design_docs
                if isinstance(item, dict)
            ]
        return payload

    def _build_replay_message(self, message: ConversationMessage) -> dict[str, object]:
        metadata = dict(message.metadata or {})
        replay_metadata = {
            "file_path": metadata.get("file_path"),
            "rule_screening": metadata.get("rule_screening"),
        }
        return {
            "message_id": message.message_id,
            "review_id": message.review_id,
            "issue_id": message.issue_id,
            "expert_id": message.expert_id,
            "message_type": message.message_type,
            "content": "",
            "created_at": message.created_at,
            "metadata": {key: value for key, value in replay_metadata.items() if value is not None},
        }

    def _build_process_message(self, message: ConversationMessage) -> dict[str, object]:
        allowed_metadata_keys = {
            "decision",
            "file_path",
            "line_start",
            "active_skills",
            "design_alignment_status",
            "design_doc_titles",
            "skill_name",
            "target_expert_id",
            "target_expert_name",
            "tool_name",
            "analysis_mode",
            "bound_documents",
            "business_changed_files",
            "changed_file_count",
            "changed_files",
            "compare_mode",
            "expert_execution_elapsed_ms",
            "expert_job_count",
            "input_completeness",
            "issue_filter_decisions",
            "knowledge_context",
            "matched_rules",
            "mode",
            "model",
            "phase",
            "platform_kind",
            "provider",
            "reply_to_expert_id",
            "review_inputs",
            "review_url",
            "routing_elapsed_ms",
            "rule_based_reasoning",
            "rule_screening",
            "rule_screening_batch",
            "selected_expert_ids",
            "selected_experts",
            "selection_elapsed_ms",
            "skill_result",
            "skipped_experts",
            "source_ref",
            "target_hunk",
            "target_ref",
            "title",
            "tool_result",
            "violated_guidelines",
        }
        metadata = dict(message.metadata or {})
        compact_metadata = {
            key: metadata.get(key)
            for key in allowed_metadata_keys
            if metadata.get(key) is not None
        }
        content = self._clip_process_message_content(message.content)
        return {
            "message_id": message.message_id,
            "review_id": message.review_id,
            "issue_id": message.issue_id,
            "expert_id": message.expert_id,
            "message_type": message.message_type,
            "content": content,
            "created_at": message.created_at,
            "metadata": compact_metadata,
        }

    def _clip_process_message_content(self, content: str) -> str:
        text = str(content or "")
        max_chars = self._process_message_max_chars()
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}\n\n[过程消息过长，前端已截断展示]"

    def _process_message_max_chars(self) -> int:
        raw = str(os.getenv("PROCESS_MESSAGE_MAX_CHARS", "")).strip()
        if raw:
            try:
                return max(1000, min(200_000, int(raw)))
            except ValueError:
                return 20_000
        return 20_000

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

    def _auto_queue_key(self, merge_request: OpenMergeRequest) -> str:
        if merge_request.head_sha:
            return f"url:{merge_request.mr_url}#sha:{merge_request.head_sha}"
        return f"url:{merge_request.mr_url}"

    def record_human_decision(
        self, review_id: str, issue_id: str, decision: str, comment: str
    ) -> ReviewTask:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        issues = self.issue_repo.list(review_id)
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
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = self._duration_seconds(review.started_at or review.created_at, review.completed_at)
        review.report_summary = build_report_summary(
            review=review,
            finding_count=len(self.list_findings(review_id)),
            issue_count=len(updated_issues),
            pending_human_count=len(pending_ids),
        )
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.artifact_service.publish(review, updated_issues)
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


review_service = ReviewService()
