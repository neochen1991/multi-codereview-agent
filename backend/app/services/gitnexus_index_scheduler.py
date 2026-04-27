from __future__ import annotations

import os
import shutil
import subprocess
import threading
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.repositories.fs import read_json, write_json
from app.services.memory_probe import MemoryProbe
from app.services.review_service import ReviewService

logger = logging.getLogger(__name__)


class GitNexusIndexScheduler:
    """后台定时执行 GitNexus 建图。

    该调度器默认关闭，避免开发环境或未安装 GitNexus 时影响系统启动。
    开启方式：

    - `GITNEXUS_INDEX_ENABLED=true`
    - 可选：`GITNEXUS_INDEX_INTERVAL_SECONDS=3600`
    - 可选：`GITNEXUS_INDEX_TIMEOUT_SECONDS=900`
    """

    COMMAND = ["gitnexus", "analyze"]

    def __init__(self, review_service: ReviewService) -> None:
        self._review_service = review_service
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual_lock = threading.Lock()
        self._manual_thread: threading.Thread | None = None

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
                logger.exception("gitnexus index scheduler tick failed error=%s", error)
                MemoryProbe.log("gitnexus_index_scheduler.error", error=error.__class__.__name__)
            interval = max(60, int(os.getenv("GITNEXUS_INDEX_INTERVAL_SECONDS", "3600") or 3600))
            self._stop_event.wait(interval)

    def tick(self) -> dict[str, object]:
        runtime = self._review_service.get_runtime_settings()
        repo_path = self._resolve_repo_path(runtime)
        status_path = self._status_path()
        binary_path = shutil.which("gitnexus") or ""
        if not repo_path:
            status = self._status(
                "skipped",
                "未配置本地代码仓路径，跳过 GitNexus 建图。",
                gitnexus_installed=bool(binary_path),
                gitnexus_command="gitnexus analyze",
                gitnexus_path=binary_path,
            )
            write_json(status_path, status)
            return status
        repo_dir = Path(repo_path).expanduser()
        if not repo_dir.exists():
            status = self._status(
                "failed",
                f"配置的本地代码仓路径不存在：{repo_dir}",
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                gitnexus_installed=bool(binary_path),
                gitnexus_command="gitnexus analyze",
                gitnexus_path=binary_path,
            )
            write_json(status_path, status)
            logger.error("gitnexus index skipped because repo path does not exist repo_path=%s", repo_dir)
            return status
        if not repo_dir.is_dir():
            status = self._status(
                "failed",
                f"配置的本地代码仓路径不是目录：{repo_dir}",
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                gitnexus_installed=bool(binary_path),
                gitnexus_command="gitnexus analyze",
                gitnexus_path=binary_path,
            )
            write_json(status_path, status)
            logger.error("gitnexus index skipped because repo path is not a directory repo_path=%s", repo_dir)
            return status
        if not binary_path:
            status = self._status(
                "skipped",
                "当前机器未预装 GitNexus，跳过建图。请先安装 GitNexus CLI，再执行图谱建立。",
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                gitnexus_installed=False,
                gitnexus_command="gitnexus analyze",
                gitnexus_path="",
            )
            write_json(status_path, status)
            return status
        timeout = max(60, int(os.getenv("GITNEXUS_INDEX_TIMEOUT_SECONDS", "900") or 900))
        command = list(self.COMMAND)
        try:
            completed = subprocess.run(
                command,
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as error:
            status = self._status(
                "failed",
                f"GitNexus 建图启动失败：{error}",
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                gitnexus_installed=bool(binary_path),
                gitnexus_command="gitnexus analyze",
                gitnexus_path=binary_path,
                error_type="FileNotFoundError",
            )
            write_json(status_path, status)
            logger.exception(
                "gitnexus index start failed repo_path=%s command=%s error=%s",
                repo_dir,
                " ".join(command),
                error,
            )
            return status
        state = "ready" if completed.returncode == 0 else "failed"
        registry_registered, registry_path = self._registry_status(str(repo_dir))
        if completed.returncode == 0 and registry_registered:
            message = "GitNexus 建图完成，仓库已写入官方 registry。"
        elif completed.returncode == 0:
            message = "GitNexus 建图完成，但未在官方 registry 中发现当前仓库，建议执行 gitnexus setup 后重新 analyze。"
        else:
            message = "GitNexus 建图失败。"
        status = self._status(
            state,
            message,
            repo_path=str(repo_dir),
            repo_name=repo_dir.name,
            indexed_at=datetime.now(UTC).isoformat() if completed.returncode == 0 else "",
            commit=self._current_commit(str(repo_dir)),
            graph_dir=str(repo_dir / ".gitnexus"),
            graph_dir_exists=(repo_dir / ".gitnexus").exists(),
            gitnexus_installed=True,
            gitnexus_command="gitnexus analyze",
            gitnexus_path=binary_path,
            registry_path=registry_path,
            registry_registered=registry_registered,
            return_code=completed.returncode,
            stdout=(completed.stdout or "")[-2000:],
            stderr=(completed.stderr or "")[-2000:],
        )
        write_json(status_path, status)
        return status

    def status(self) -> dict[str, object]:
        """返回最近一次 GitNexus 建图状态，供设置页展示。"""

        status_path = self._status_path()
        if not status_path.exists():
            binary_path = shutil.which("gitnexus") or ""
            return self._status(
                "idle",
                "尚未执行 GitNexus 建图。",
                gitnexus_installed=bool(binary_path),
                gitnexus_command="gitnexus analyze",
                gitnexus_path=binary_path,
            )
        try:
            payload = read_json(status_path)
        except Exception:
            binary_path = shutil.which("gitnexus") or ""
            return self._status(
                "unknown",
                "GitNexus 建图状态文件读取失败。",
                gitnexus_installed=bool(binary_path),
                gitnexus_command="gitnexus analyze",
                gitnexus_path=binary_path,
            )
        if isinstance(payload, dict):
            payload.setdefault("gitnexus_installed", bool(shutil.which("gitnexus") or ""))
            payload.setdefault("gitnexus_command", "gitnexus analyze")
            payload.setdefault("gitnexus_path", shutil.which("gitnexus") or "")
            return dict(payload)
        binary_path = shutil.which("gitnexus") or ""
        return self._status(
            "unknown",
            "GitNexus 建图状态格式异常。",
            gitnexus_installed=bool(binary_path),
            gitnexus_command="gitnexus analyze",
            gitnexus_path=binary_path,
        )

    def trigger_manual_index(self) -> dict[str, object]:
        """手动触发一次建图，避免用户只能等待后台定时任务。"""

        with self._manual_lock:
            if self._manual_thread and self._manual_thread.is_alive():
                return self.status()
            runtime = self._review_service.get_runtime_settings()
            repo_path = self._resolve_repo_path(runtime)
            binary_path = shutil.which("gitnexus") or ""
            running_status = self._status(
                "running",
                "GitNexus 手动建图已触发，后台正在执行。",
                repo_path=repo_path,
                repo_name=Path(repo_path).name if repo_path else "",
                trigger="manual",
                gitnexus_installed=bool(binary_path),
                gitnexus_command="gitnexus analyze",
                gitnexus_path=binary_path,
            )
            write_json(self._status_path(), running_status)
            self._manual_thread = threading.Thread(
                target=self._run_manual_tick,
                name="gitnexus-manual-index",
                daemon=True,
            )
            self._manual_thread.start()
            return running_status

    def _run_manual_tick(self) -> None:
        try:
            self.tick()
        except Exception as error:
            binary_path = shutil.which("gitnexus") or ""
            status = self._status(
                "failed",
                f"GitNexus 手动建图异常：{error}",
                gitnexus_installed=bool(binary_path),
                gitnexus_command="gitnexus analyze",
                gitnexus_path=binary_path,
                error_type=error.__class__.__name__,
            )
            write_json(self._status_path(), status)
            logger.exception("gitnexus manual index failed error=%s", error)

    def _enabled(self) -> bool:
        return str(os.getenv("GITNEXUS_INDEX_ENABLED", "")).strip().lower() in {"1", "true", "on", "yes"}

    def _resolve_repo_path(self, runtime) -> str:
        raw = str(getattr(runtime, "code_repo_local_path", "") or "").strip()
        if not raw:
            return ""
        return str(Path(raw).expanduser())

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

    def _registry_status(self, repo_path: str) -> tuple[bool, str]:
        registry_path = Path.home() / ".gitnexus" / "registry.json"
        if not registry_path.exists():
            return False, str(registry_path)
        try:
            payload = read_json(registry_path)
        except Exception:
            return False, str(registry_path)
        entries: list[dict[str, object]] = []
        if isinstance(payload, list):
            entries = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            for key in ("repos", "repositories", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    entries = [item for item in value if isinstance(item, dict)]
                    break
        repo_path_resolved = str(Path(repo_path).resolve())
        for item in entries:
            candidate_path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
            if candidate_path and str(Path(candidate_path).resolve()) == repo_path_resolved:
                return True, str(registry_path)
        return False, str(registry_path)

    def _status(self, state: str, message: str, **extra: object) -> dict[str, object]:
        return {
            "state": state,
            "message": message,
            "updated_at": datetime.now(UTC).isoformat(),
            **extra,
        }
