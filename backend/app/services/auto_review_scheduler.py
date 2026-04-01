from __future__ import annotations

import logging
import os
import threading

from app.services.review_service import ReviewService
from app.services.memory_probe import MemoryProbe

logger = logging.getLogger(__name__)


class AutoReviewScheduler:
    """系统启动后的自动 MR 拉取与串行审核调度器。"""

    def __init__(self, review_service: ReviewService) -> None:
        self._review_service = review_service
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动后台轮询线程。"""

        MemoryProbe.log("scheduler.start.begin")
        if os.getenv("PYTEST_CURRENT_TEST"):
            logger.info("auto review scheduler skipped in pytest runtime")
            MemoryProbe.log("scheduler.start.skipped_pytest")
            return
        recovered = self._review_service.recover_interrupted_reviews()
        if recovered:
            logger.warning(
                "auto review scheduler recovered interrupted reviews review_ids=%s",
                [item.review_id for item in recovered],
            )
        MemoryProbe.log("scheduler.start.after_recover", recovered_count=len(recovered))
        if self._thread and self._thread.is_alive():
            MemoryProbe.log("scheduler.start.already_running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="auto-review-scheduler", daemon=True)
        self._thread.start()
        logger.info("auto review scheduler started")
        MemoryProbe.log("scheduler.start.started")

    def stop(self) -> None:
        """停止后台轮询线程。"""

        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("auto review scheduler stopped")
        MemoryProbe.log("scheduler.stop")

    def tick(self) -> None:
        """执行一次：拉取开放 MR 入队 + 启动下一条待处理任务。"""

        runtime = self._review_service.get_runtime_settings()
        MemoryProbe.log(
            "scheduler.tick.start",
            auto_review_enabled=runtime.auto_review_enabled,
            poll_interval=runtime.auto_review_poll_interval_seconds,
        )
        if not runtime.auto_review_enabled:
            MemoryProbe.log("scheduler.tick.auto_review_disabled")
            return
        repo_url = self._review_service.resolve_auto_review_repo_url(runtime)
        if not repo_url:
            logger.warning("auto review enabled but repo url is empty, skip this tick")
            MemoryProbe.log("scheduler.tick.empty_repo_url")
            return
        created = self._review_service.enqueue_open_merge_requests(repo_url)
        MemoryProbe.log("scheduler.tick.after_enqueue", created_count=len(created), repo_url=repo_url)
        if created:
            logger.info(
                "auto review queue received %s new items review_ids=%s",
                len(created),
                [item.review_id for item in created],
            )
        started = self._review_service.start_next_pending_review()
        MemoryProbe.log(
            "scheduler.tick.after_start_next",
            started_review_id=started.review_id if started is not None else "",
        )
        if started is not None:
            logger.info("auto review queue started review_id=%s", started.review_id)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            interval = 120
            try:
                runtime = self._review_service.get_runtime_settings()
                interval = max(15, int(runtime.auto_review_poll_interval_seconds or 120))
                MemoryProbe.log("scheduler.loop.before_tick", interval=interval)
                self.tick()
                MemoryProbe.log("scheduler.loop.after_tick", interval=interval)
            except Exception as error:
                logger.exception("auto review scheduler tick failed error=%s", error)
                MemoryProbe.log("scheduler.loop.error", error=error.__class__.__name__)
            self._stop_event.wait(interval)
