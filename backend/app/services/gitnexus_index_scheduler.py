from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.repositories.fs import write_json
from app.services.memory_probe import MemoryProbe
from app.services.review_service import ReviewService


class GitNexusIndexScheduler:
    """后台定时执行 GitNexus 建图。

    该调度器默认关闭，避免开发环境或未安装 GitNexus 时影响系统启动。
    开启方式：

    - `GITNEXUS_INDEX_ENABLED=true`
    - 可选：`GITNEXUS_INDEX_INTERVAL_SECONDS=3600`
    - 可选：`GITNEXUS_INDEX_TIMEOUT_SECONDS=900`
    """

    def __init__(self, review_service: ReviewService) -> None:
        self._review_service = review_service
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        if not self._enabled():
            MemoryProbe.log("gitnexus_index_scheduler.start.disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="gitnexus-index-scheduler", daemon=True)
        self._thread.start()
        MemoryProbe.log("gitnexus_index_scheduler.start")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        MemoryProbe.log("gitnexus_index_scheduler.stop")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as error:
                MemoryProbe.log("gitnexus_index_scheduler.error", error=error.__class__.__name__)
            interval = max(60, int(os.getenv("GITNEXUS_INDEX_INTERVAL_SECONDS", "3600") or 3600))
            self._stop_event.wait(interval)

    def tick(self) -> dict[str, object]:
        runtime = self._review_service.get_runtime_settings()
        repo_path = self._resolve_repo_path(runtime)
        status_path = self._status_path()
        if not repo_path:
            status = self._status("skipped", "未配置本地代码仓路径，跳过 GitNexus 建图。")
            write_json(status_path, status)
            return status
        if shutil.which("npx") is None:
            status = self._status("skipped", "当前机器未找到 npx，跳过 GitNexus 建图。", repo_path=repo_path)
            write_json(status_path, status)
            return status
        timeout = max(60, int(os.getenv("GITNEXUS_INDEX_TIMEOUT_SECONDS", "900") or 900))
        command = ["npx", "gitnexus", "analyze"]
        completed = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        state = "ready" if completed.returncode == 0 else "failed"
        message = "GitNexus 建图完成。" if completed.returncode == 0 else "GitNexus 建图失败。"
        status = self._status(
            state,
            message,
            repo_path=repo_path,
            repo_name=Path(repo_path).name,
            indexed_at=datetime.now(UTC).isoformat() if completed.returncode == 0 else "",
            commit=self._current_commit(repo_path),
            graph_dir=str(Path(repo_path) / ".gitnexus"),
            graph_dir_exists=(Path(repo_path) / ".gitnexus").exists(),
            return_code=completed.returncode,
            stdout=(completed.stdout or "")[-2000:],
            stderr=(completed.stderr or "")[-2000:],
        )
        write_json(status_path, status)
        return status

    def _enabled(self) -> bool:
        return str(os.getenv("GITNEXUS_INDEX_ENABLED", "")).strip().lower() in {"1", "true", "on", "yes"}

    def _resolve_repo_path(self, runtime) -> str:
        return str(getattr(runtime, "code_repo_local_path", "") or "").strip()

    def _status_path(self) -> Path:
        return self._review_service.storage_root / "gitnexus" / "index_status.json"

    def _current_commit(self, repo_path: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return ""
        if completed.returncode != 0:
            return ""
        return str(completed.stdout or "").strip()

    def _status(self, state: str, message: str, **extra: object) -> dict[str, object]:
        return {
            "state": state,
            "message": message,
            "updated_at": datetime.now(UTC).isoformat(),
            **extra,
        }
