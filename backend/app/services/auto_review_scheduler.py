from __future__ import annotations

import logging
import os
import threading

from app.services.review_service import ReviewService

logger = logging.getLogger(__name__)


class AutoReviewScheduler:
    """系统启动后的自动 MR 拉取与串行审核调度器。"""

    def __init__(self, review_service: ReviewService) -> None:
        self._review_service = review_service
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动后台轮询线程。"""

        if os.getenv("PYTEST_CURRENT_TEST"):
            logger.info("auto review scheduler skipped in pytest runtime")
            return
        recovered = self._review_service.recover_interrupted_reviews()
        if recovered:
            logger.warning(
                "auto review scheduler recovered interrupted reviews review_ids=%s",
                [item.review_id for item in recovered],
            )
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="auto-review-scheduler", daemon=True)
        self._thread.start()
        logger.info("auto review scheduler started")

    def stop(self) -> None:
        """停止后台轮询线程。"""

        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("auto review scheduler stopped")

    def tick(self) -> None:
        """执行一次：拉取开放 MR 入队 + 启动下一条待处理任务。"""

        runtime = self._review_service.get_runtime_settings()
        if not runtime.auto_review_enabled:
            return
        repo_url = self._review_service.resolve_auto_review_repo_url(runtime)
        if not repo_url:
            logger.warning("auto review enabled but repo url is empty, skip this tick")
            return
        created = self._review_service.enqueue_open_merge_requests(repo_url)
        if created:
            logger.info(
                "auto review queue received %s new items review_ids=%s",
                len(created),
                [item.review_id for item in created],
            )
        started = self._review_service.start_next_pending_review()
        if started is not None:
            logger.info("auto review queue started review_id=%s", started.review_id)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            interval = 120
            try:
                runtime = self._review_service.get_runtime_settings()
                interval = max(15, int(runtime.auto_review_poll_interval_seconds or 120))
                self.tick()
            except Exception as error:
                logger.exception("auto review scheduler tick failed error=%s", error)
            self._stop_event.wait(interval)
