from __future__ import annotations

import os
import subprocess
import threading
import logging
import multiprocessing as mp
from datetime import UTC, datetime
from pathlib import Path

from app.repositories.fs import read_json, write_json
from app.services.command_resolver import resolve_executable
from app.services.gitnexus_impact_service import _normalize_path_for_compare, _parse_command_text
from app.services.memory_probe import MemoryProbe
from app.services.review_service import ReviewService

logger = logging.getLogger(__name__)


def _run_gitnexus_index_in_subprocess(storage_root: str, repository_id: str = "") -> None:
    """在独立子进程中执行一次 GitNexus 建图，避免阻塞主服务进程。"""

    service = ReviewService(storage_root=Path(storage_root))
    scheduler = GitNexusIndexScheduler(service)
    scheduler.tick(repository_id)


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
        self._manual_lock = threading.Lock()
        self._manual_process: mp.Process | None = None
        self._manual_repository_id = ""

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
                runtime = self._review_service.get_runtime_settings()
                repositories = [
                    repo
                    for repo in runtime.enabled_repositories()
                    if bool(getattr(repo, "gitnexus_enabled", True)) and str(getattr(repo, "repository_id", "") or "").strip()
                ]
                if repositories:
                    for repository in repositories:
                        self.tick(str(repository.repository_id))
                else:
                    self.tick()
            except Exception as error:
                logger.exception("gitnexus index scheduler tick failed error=%s", error)
                MemoryProbe.log("gitnexus_index_scheduler.error", error=error.__class__.__name__)
            interval = max(60, int(os.getenv("GITNEXUS_INDEX_INTERVAL_SECONDS", "3600") or 3600))
            self._stop_event.wait(interval)

    def tick(self, repository_id: str = "") -> dict[str, object]:
        runtime = self._review_service.get_runtime_settings()
        resolved_repository_id = self._resolve_repository_id(runtime, repository_id)
        repo_path = self._resolve_repo_path(runtime, resolved_repository_id)
        status_path = self._status_path(resolved_repository_id)
        command = self._index_command()
        command_text = self._command_text(command)
        binary_path = self._command_path(command)
        command_available = self._command_available(command)
        if not repo_path:
            status = self._status(
                "skipped",
                "未配置本地代码仓路径，跳过 GitNexus 建图。",
                repository_id=resolved_repository_id,
                gitnexus_installed=command_available,
                gitnexus_command=command_text,
                gitnexus_path=binary_path,
            )
            write_json(status_path, status)
            return status
        repo_dir = Path(repo_path).expanduser()
        if not repo_dir.exists():
            status = self._status(
                "failed",
                f"配置的本地代码仓路径不存在：{repo_dir}",
                repository_id=resolved_repository_id,
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                gitnexus_installed=command_available,
                gitnexus_command=command_text,
                gitnexus_path=binary_path,
            )
            write_json(status_path, status)
            logger.error("gitnexus index skipped because repo path does not exist repo_path=%s", repo_dir)
            return status
        if not repo_dir.is_dir():
            status = self._status(
                "failed",
                f"配置的本地代码仓路径不是目录：{repo_dir}",
                repository_id=resolved_repository_id,
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                gitnexus_installed=command_available,
                gitnexus_command=command_text,
                gitnexus_path=binary_path,
            )
            write_json(status_path, status)
            logger.error("gitnexus index skipped because repo path is not a directory repo_path=%s", repo_dir)
            return status
        if not command_available:
            status = self._status(
                "skipped",
                "当前机器未预装 GitNexus，跳过建图。请先安装 GitNexus CLI，再执行图谱建立。",
                repository_id=resolved_repository_id,
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                gitnexus_installed=False,
                gitnexus_command=command_text,
                gitnexus_path=binary_path,
            )
            write_json(status_path, status)
            return status
        timeout = max(60, int(os.getenv("GITNEXUS_INDEX_TIMEOUT_SECONDS", "900") or 900))
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
            executable = command[0] if command else "gitnexus"
            status = self._status(
                "failed",
                (
                    "GitNexus 建图启动失败：系统找不到指定的文件。"
                    f" 请优先检查 GitNexus 可执行命令是否可用，以及本地代码仓路径是否有效。"
                    f" executable={executable} repo_path={repo_dir} raw_error={error}"
                ),
                repository_id=resolved_repository_id,
                repo_path=str(repo_dir),
                repo_name=repo_dir.name,
                gitnexus_installed=command_available,
                gitnexus_command=command_text,
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
            repository_id=resolved_repository_id,
            repo_path=str(repo_dir),
            repo_name=repo_dir.name,
            indexed_at=datetime.now(UTC).isoformat() if completed.returncode == 0 else "",
            commit=self._current_commit(str(repo_dir)),
            graph_dir=str(repo_dir / ".gitnexus"),
            graph_dir_exists=(repo_dir / ".gitnexus").exists(),
            gitnexus_installed=True,
            gitnexus_command=command_text,
            gitnexus_path=binary_path,
            registry_path=registry_path,
            registry_registered=registry_registered,
            return_code=completed.returncode,
            stdout=(completed.stdout or "")[-2000:],
            stderr=(completed.stderr or "")[-2000:],
        )
        write_json(status_path, status)
        return status

    def status(self, repository_id: str = "") -> dict[str, object]:
        """返回最近一次 GitNexus 建图状态，供设置页展示。"""

        runtime = self._review_service.get_runtime_settings()
        resolved_repository_id = self._resolve_repository_id(runtime, repository_id)
        status_path = self._status_path(resolved_repository_id)
        command = self._index_command()
        command_text = self._command_text(command)
        binary_path = self._command_path(command)
        command_available = self._command_available(command)
        if not status_path.exists():
            return self._status(
                "idle",
                "尚未执行 GitNexus 建图。",
                repository_id=resolved_repository_id,
                gitnexus_installed=command_available,
                gitnexus_command=command_text,
                gitnexus_path=binary_path,
            )
        try:
            payload = read_json(status_path)
        except Exception:
            return self._status(
                "unknown",
                "GitNexus 建图状态文件读取失败。",
                repository_id=resolved_repository_id,
                gitnexus_installed=command_available,
                gitnexus_command=command_text,
                gitnexus_path=binary_path,
            )
        if isinstance(payload, dict):
            payload.setdefault("gitnexus_installed", command_available)
            payload.setdefault("gitnexus_command", command_text)
            payload.setdefault("gitnexus_path", binary_path)
            payload.setdefault("repository_id", resolved_repository_id)
            return dict(payload)
        return self._status(
            "unknown",
            "GitNexus 建图状态格式异常。",
            repository_id=resolved_repository_id,
            gitnexus_installed=command_available,
            gitnexus_command=command_text,
            gitnexus_path=binary_path,
        )

    def trigger_manual_index(self, repository_id: str = "") -> dict[str, object]:
        """手动触发一次建图，避免用户只能等待后台定时任务。"""

        with self._manual_lock:
            if self._manual_process and self._manual_process.is_alive():
                return self.status(repository_id)
            runtime = self._review_service.get_runtime_settings()
            resolved_repository_id = self._resolve_repository_id(runtime, repository_id)
            repo_path = self._resolve_repo_path(runtime, resolved_repository_id)
            command = self._index_command()
            command_text = self._command_text(command)
            binary_path = self._command_path(command)
            command_available = self._command_available(command)
            if not repo_path:
                skipped = self._status(
                    "skipped",
                    "未配置本地代码仓路径，无法触发 GitNexus 手动建图。",
                    repository_id=resolved_repository_id,
                    trigger="manual",
                    gitnexus_installed=command_available,
                    gitnexus_command=command_text,
                    gitnexus_path=binary_path,
                )
                write_json(self._status_path(resolved_repository_id), skipped)
                return skipped
            running_status = self._status(
                "running",
                "GitNexus 手动建图已触发，后台子进程正在执行。",
                repo_path=repo_path,
                repo_name=Path(repo_path).name if repo_path else "",
                repository_id=resolved_repository_id,
                trigger="manual",
                gitnexus_installed=command_available,
                gitnexus_command=command_text,
                gitnexus_path=binary_path,
            )
            try:
                process_ctx = mp.get_context("spawn")
                process = process_ctx.Process(
                    target=_run_gitnexus_index_in_subprocess,
                    args=(str(self._review_service.storage_root), resolved_repository_id),
                    name="gitnexus-manual-index",
                    daemon=False,
                )
                process.start()
            except Exception as error:
                failed = self._status(
                    "failed",
                    f"GitNexus 手动建图子进程启动失败：{error}",
                    repo_path=repo_path,
                    repo_name=Path(repo_path).name if repo_path else "",
                    repository_id=resolved_repository_id,
                    trigger="manual",
                    gitnexus_installed=command_available,
                    gitnexus_command=command_text,
                    gitnexus_path=binary_path,
                    error_type=error.__class__.__name__,
                )
                write_json(self._status_path(resolved_repository_id), failed)
                logger.exception("gitnexus manual index process start failed error=%s", error)
                return failed

            running_status["worker_pid"] = int(process.pid or 0)
            write_json(self._status_path(resolved_repository_id), running_status)
            self._manual_process = process
            self._manual_repository_id = resolved_repository_id
            threading.Thread(target=self._watch_manual_process, name="gitnexus-manual-index-watch", daemon=True).start()
            return running_status

    def _watch_manual_process(self) -> None:
        process = self._manual_process
        if process is None:
            return
        try:
            process.join()
            if int(process.exitcode or 0) != 0:
                repository_id = self._manual_repository_id
                current = self.status(repository_id)
                if str(current.get("state") or "") == "running":
                    command = self._index_command()
                    failed = self._status(
                        "failed",
                        f"GitNexus 手动建图子进程异常退出，exit_code={process.exitcode}",
                        repository_id=repository_id,
                        repo_path=str(current.get("repo_path") or ""),
                        repo_name=str(current.get("repo_name") or ""),
                        trigger="manual",
                        gitnexus_installed=self._command_available(command),
                        gitnexus_command=self._command_text(command),
                        gitnexus_path=self._command_path(command),
                        error_type="SubprocessExit",
                        worker_pid=int(process.pid or 0),
                    )
                    write_json(self._status_path(str(current.get("repository_id") or "")), failed)
                logger.error(
                    "gitnexus manual index subprocess exited abnormally pid=%s exit_code=%s",
                    process.pid,
                    process.exitcode,
                )
        finally:
            with self._manual_lock:
                if self._manual_process is process:
                    self._manual_process = None
                    self._manual_repository_id = ""

    def _enabled(self) -> bool:
        return str(os.getenv("GITNEXUS_INDEX_ENABLED", "")).strip().lower() in {"1", "true", "on", "yes"}

    def _index_command(self) -> list[str]:
        raw = str(os.getenv("GITNEXUS_ANALYZE_COMMAND") or "").strip()
        if raw:
            parsed = _parse_command_text(raw)
            if parsed:
                return parsed
        binary = str(os.getenv("GITNEXUS_BIN") or "").strip()
        if binary:
            return [binary, "analyze"]
        binary = resolve_executable("gitnexus") or "gitnexus"
        return [binary, "analyze"]

    def _command_text(self, command: list[str]) -> str:
        return " ".join(str(part) for part in command if str(part))

    def _command_path(self, command: list[str]) -> str:
        executable = str(command[0] if command else "").strip()
        if not executable:
            return ""
        return resolve_executable(executable) or executable

    def _command_available(self, command: list[str]) -> bool:
        executable = str(command[0] if command else "").strip()
        if not executable:
            return False
        return bool(resolve_executable(executable))

    def _resolve_repository_id(self, runtime, repository_id: str = "") -> str:
        raw = str(repository_id or "").strip()
        if raw:
            return raw
        return str(getattr(runtime, "default_repository_id", "") or "").strip()

    def _resolve_repo_path(self, runtime, repository_id: str = "") -> str:
        normalized_repository_id = str(repository_id or "").strip()
        repositories = list(getattr(runtime, "code_repositories", []) or [])
        if normalized_repository_id and repositories and not any(repo.repository_id == normalized_repository_id for repo in repositories):
            return ""
        repository = runtime.resolve_repository(repository_id=normalized_repository_id)
        raw = str((repository.local_path if repository is not None else "") or getattr(runtime, "code_repo_local_path", "") or "").strip()
        if not raw:
            return ""
        return str(Path(raw).expanduser())

    def _status_path(self, repository_id: str = "") -> Path:
        normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(repository_id or "").strip())
        if normalized:
            return self._review_service.storage_root / "gitnexus" / normalized / "index_status.json"
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
        registry_path = self._registry_path()
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
        repo_path_resolved = _normalize_path_for_compare(repo_path)
        for item in entries:
            candidate_path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
            if candidate_path and _normalize_path_for_compare(candidate_path) == repo_path_resolved:
                return True, str(registry_path)
        return False, str(registry_path)

    def _registry_path(self) -> Path:
        raw_registry = str(os.getenv("GITNEXUS_REGISTRY_PATH") or "").strip()
        if raw_registry:
            return Path(raw_registry).expanduser()
        raw_home = str(os.getenv("GITNEXUS_HOME") or "").strip()
        if raw_home:
            return Path(raw_home).expanduser() / "registry.json"
        return Path.home() / ".gitnexus" / "registry.json"

    def _status(self, state: str, message: str, **extra: object) -> dict[str, object]:
        return {
            "state": state,
            "message": message,
            "updated_at": datetime.now(UTC).isoformat(),
            **extra,
        }
