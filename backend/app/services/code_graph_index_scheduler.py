from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.repositories.fs import read_json, write_json
from app.services.code_graph.index_service import CodeGraphIndexService
from app.services.code_graph.java_tree_sitter_parser import JavaTreeSitterParser, tree_sitter_java_dependency_status
from app.services.review_service import ReviewService

logger = logging.getLogger(__name__)


class CodeGraphIndexScheduler:
    """Manual Tree-sitter code graph builder used by the settings page."""

    def __init__(self, review_service: ReviewService) -> None:
        self._review_service = review_service
        self._manual_lock = threading.Lock()
        self._manual_thread: threading.Thread | None = None
        self._manual_repository_id = ""

    def status(self, repository_id: str = "") -> dict[str, object]:
        runtime = self._review_service.get_runtime_settings()
        resolved_repository_id = self._resolve_repository_id(runtime, repository_id)
        repo_path = self._resolve_repo_path(runtime, resolved_repository_id)
        status_path = self._status_path(resolved_repository_id)
        if status_path.exists():
            try:
                status = dict(read_json(status_path))
                status["repository_id"] = resolved_repository_id
                if repo_path:
                    status["repo_path"] = repo_path
                    status["repo_name"] = Path(repo_path).name
                status.update(self._graph_details(repo_path))
                dependency_status = self._dependency_status()
                status["tree_sitter_installed"] = dependency_status["installed"]
                status["tree_sitter_parser_available"] = dependency_status["parser_available"]
                status["dependency_checks"] = dependency_status["checks"]
                if str(status.get("state") or "") == "running" and not self._is_running(resolved_repository_id):
                    status["state"] = "unknown"
                    status["message"] = "上次 Tree-sitter 建图任务未确认完成状态，请刷新或重新建立图谱。"
                if str(status.get("state") or "") == "ready" and status.get("graph_db_exists") is False:
                    status["state"] = "unknown"
                    status["message"] = "未在当前代码仓路径发现 Tree-sitter 图谱，请重新建立图谱。"
                return status
            except Exception:
                logger.exception("code graph status load failed path=%s", status_path)
        dependency_status = self._dependency_status()
        if not repo_path:
            return self._status(
                "skipped",
                "未配置本地代码仓路径，无法建立 Tree-sitter 代码图谱。",
                repository_id=resolved_repository_id,
                tree_sitter_installed=dependency_status["installed"],
                tree_sitter_parser_available=dependency_status["parser_available"],
                dependency_checks=dependency_status["checks"],
            )
        details = self._graph_details(repo_path)
        if details.get("graph_db_exists"):
            return self._status(
                "ready",
                "已发现 Tree-sitter 本地代码图谱。",
                repository_id=resolved_repository_id,
                repo_path=repo_path,
                repo_name=Path(repo_path).name,
                tree_sitter_installed=dependency_status["installed"],
                tree_sitter_parser_available=dependency_status["parser_available"],
                dependency_checks=dependency_status["checks"],
                indexed_at=details.get("graph_db_updated_at") or "",
                **details,
            )
        state = "idle" if dependency_status["parser_available"] else "skipped"
        message = "尚未建立 Tree-sitter 代码图谱。" if state == "idle" else self._dependency_unavailable_message(dependency_status)
        return self._status(
            state,
            message,
            repository_id=resolved_repository_id,
            repo_path=repo_path,
            repo_name=Path(repo_path).name,
            tree_sitter_installed=dependency_status["installed"],
            tree_sitter_parser_available=dependency_status["parser_available"],
            dependency_checks=dependency_status["checks"],
            **details,
        )

    def trigger_manual_index(self, repository_id: str = "") -> dict[str, object]:
        with self._manual_lock:
            runtime = self._review_service.get_runtime_settings()
            resolved_repository_id = self._resolve_repository_id(runtime, repository_id)
            if self._manual_thread and self._manual_thread.is_alive():
                running_repository_id = str(self._manual_repository_id or "").strip()
                if running_repository_id and running_repository_id != resolved_repository_id:
                    blocked = self.status(resolved_repository_id)
                    blocked.update(
                        {
                            "state": "blocked",
                            "message": f"Tree-sitter 正在为仓库 {running_repository_id} 建图，当前仓库 {resolved_repository_id} 尚未启动。请等待当前任务结束后重试。",
                            "trigger": "manual",
                            "blocked_by_repository_id": running_repository_id,
                        }
                    )
                    write_json(self._status_path(resolved_repository_id), blocked)
                    return blocked
                return self.status(resolved_repository_id)
            running = self._running_status(runtime, resolved_repository_id)
            write_json(self._status_path(resolved_repository_id), running)
            thread = threading.Thread(
                target=self._run_manual_index,
                args=(resolved_repository_id,),
                name=f"tree-sitter-code-graph-index-{resolved_repository_id or 'default'}",
                daemon=True,
            )
            self._manual_thread = thread
            self._manual_repository_id = resolved_repository_id
            thread.start()
            return running

    def tick(self, repository_id: str = "", *, trigger: str = "manual") -> dict[str, object]:
        runtime = self._review_service.get_runtime_settings()
        resolved_repository_id = self._resolve_repository_id(runtime, repository_id)
        repo_path = self._resolve_repo_path(runtime, resolved_repository_id)
        dependency_status = self._dependency_status()
        if not repo_path:
            status = self._status(
                "skipped",
                "未配置本地代码仓路径，无法建立 Tree-sitter 代码图谱。",
                repository_id=resolved_repository_id,
                trigger=trigger,
                tree_sitter_installed=dependency_status["installed"],
                tree_sitter_parser_available=dependency_status["parser_available"],
                dependency_checks=dependency_status["checks"],
            )
            write_json(self._status_path(resolved_repository_id), status)
            return status
        repo_dir = Path(repo_path).expanduser()
        base_payload = {
            "repository_id": resolved_repository_id,
            "repo_path": str(repo_dir),
            "repo_name": repo_dir.name,
            "trigger": trigger,
            "tree_sitter_installed": dependency_status["installed"],
            "tree_sitter_parser_available": dependency_status["parser_available"],
            "dependency_checks": dependency_status["checks"],
        }
        if not repo_dir.exists():
            status = self._status("failed", f"配置的本地代码仓路径不存在：{repo_dir}", **base_payload)
            write_json(self._status_path(resolved_repository_id), status)
            return status
        if not repo_dir.is_dir():
            status = self._status("failed", f"配置的本地代码仓路径不是目录：{repo_dir}", **base_payload)
            write_json(self._status_path(resolved_repository_id), status)
            return status
        if not dependency_status["parser_available"]:
            status = self._status("failed", self._dependency_unavailable_message(dependency_status), **base_payload)
            write_json(self._status_path(resolved_repository_id), status)
            return status
        try:
            result = CodeGraphIndexService(repo_root=repo_dir, parser=JavaTreeSitterParser()).full_build(
                repository_id=resolved_repository_id,
                languages=["java"],
            )
            details = self._graph_details(str(repo_dir))
            indexed = int(result.get("indexed_file_count") or 0)
            skipped = int(result.get("skipped_unchanged_file_count") or 0)
            failed = int(result.get("failed_file_count") or 0)
            state = str(result.get("state") or "ready")
            message = f"Tree-sitter 代码图谱建立完成：本次索引 {indexed} 个文件，跳过 {skipped} 个未变更文件。"
            if failed:
                message += f" 其中 {failed} 个文件解析失败，已保留可用图谱。"
            status = self._status(
                state,
                message,
                indexed_at=datetime.now(UTC).isoformat(),
                selected_file_count=int(result.get("selected_file_count") or 0),
                indexed_file_count=indexed,
                skipped_unchanged_file_count=skipped,
                failed_file_count=failed,
                failed_files=list(result.get("failed_files") or [])[:20],
                **base_payload,
                **details,
            )
            write_json(self._status_path(resolved_repository_id), status)
            logger.info("tree_sitter code graph index finished repository_id=%s status=%s", resolved_repository_id, status)
            return status
        except Exception as error:
            logger.exception("tree_sitter code graph index failed repository_id=%s repo_path=%s", resolved_repository_id, repo_dir)
            status = self._status(
                "failed",
                f"Tree-sitter 代码图谱建立失败：{error}",
                error_type=error.__class__.__name__,
                **base_payload,
            )
            write_json(self._status_path(resolved_repository_id), status)
            return status

    def _run_manual_index(self, repository_id: str) -> None:
        current = threading.current_thread()
        try:
            self.tick(repository_id, trigger="manual")
        finally:
            with self._manual_lock:
                if self._manual_thread is current:
                    self._manual_thread = None
                    self._manual_repository_id = ""

    def _running_status(self, runtime: object, repository_id: str) -> dict[str, object]:
        repo_path = self._resolve_repo_path(runtime, repository_id)
        dependency_status = self._dependency_status()
        return self._status(
            "running",
            "Tree-sitter 代码图谱建图任务已触发，后台正在解析 Java 代码。",
            repository_id=repository_id,
            repo_path=repo_path,
            repo_name=Path(repo_path).name if repo_path else "",
            trigger="manual",
            tree_sitter_installed=dependency_status["installed"],
            tree_sitter_parser_available=dependency_status["parser_available"],
            dependency_checks=dependency_status["checks"],
            **self._graph_details(repo_path),
        )

    def _is_running(self, repository_id: str) -> bool:
        return bool(
            self._manual_thread
            and self._manual_thread.is_alive()
            and str(self._manual_repository_id or "").strip() == str(repository_id or "").strip()
        )

    def _resolve_repository_id(self, runtime: object, repository_id: str = "") -> str:
        normalized = str(repository_id or "").strip()
        resolve_repository = getattr(runtime, "resolve_repository", None)
        repo = resolve_repository(repository_id=normalized) if callable(resolve_repository) else None
        if repo is not None and str(getattr(repo, "repository_id", "") or "").strip():
            return str(getattr(repo, "repository_id") or "").strip()
        if normalized:
            return normalized
        default_repository_id = str(getattr(runtime, "default_repository_id", "") or "").strip()
        if default_repository_id:
            return default_repository_id
        repositories = list(getattr(runtime, "code_repositories", []) or [])
        for item in repositories:
            candidate = str(getattr(item, "repository_id", "") or "").strip()
            if candidate:
                return candidate
        return "default-repository"

    def _resolve_repo_path(self, runtime: object, repository_id: str = "") -> str:
        resolve_repository = getattr(runtime, "resolve_repository", None)
        repo = resolve_repository(repository_id=str(repository_id or "").strip()) if callable(resolve_repository) else None
        if repo is not None and str(getattr(repo, "local_path", "") or "").strip():
            return str(getattr(repo, "local_path") or "").strip()
        return str(getattr(runtime, "code_repo_local_path", "") or "").strip()

    def _status_path(self, repository_id: str = "") -> Path:
        normalized = self._safe_repository_id(repository_id or "default-repository")
        return self._review_service.storage_root / "code_graph" / normalized / "index_status.json"

    def _graph_details(self, repo_path: str) -> dict[str, object]:
        if not repo_path:
            return {}
        repo_dir = Path(repo_path).expanduser()
        graph_dir = repo_dir / ".code-review-graph"
        graph_db = graph_dir / "graph.db"
        payload: dict[str, object] = {
            "graph_dir": str(graph_dir),
            "graph_dir_exists": graph_dir.exists(),
            "graph_db_path": str(graph_db),
            "graph_db_exists": graph_db.exists(),
        }
        if graph_db.exists():
            try:
                payload["graph_db_updated_at"] = datetime.fromtimestamp(graph_db.stat().st_mtime, UTC).isoformat()
            except OSError:
                payload["graph_db_updated_at"] = ""
            payload.update(self._graph_counts(graph_db))
        return payload

    def _graph_counts(self, graph_db: Path) -> dict[str, object]:
        try:
            with sqlite3.connect(graph_db) as conn:
                return {
                    "graph_file_count": int(conn.execute("SELECT COUNT(*) FROM file_state").fetchone()[0]),
                    "graph_node_count": int(conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]),
                    "graph_edge_count": int(conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]),
                    "graph_db_readable": True,
                }
        except Exception:
            logger.exception("tree_sitter graph db count failed path=%s", graph_db)
            return {"graph_db_readable": False}

    def _dependency_status(self) -> dict[str, object]:
        return tree_sitter_java_dependency_status()

    def _dependency_unavailable_message(self, dependency_status: dict[str, object]) -> str:
        checks = list(dependency_status.get("checks") or [])
        for check in checks:
            if str(check.get("status") or "") == "failed" and str(check.get("name") or "") == "tree_sitter_java":
                return f"Tree-sitter Java 解析依赖不可用：{check.get('message')}"
        for check in checks:
            if str(check.get("status") or "") == "failed":
                return f"Tree-sitter 代码图谱依赖不可用：{check.get('message')}"
        return "Tree-sitter Java 解析依赖不可用，请先安装 code-graph 依赖。"

    def _status(self, state: str, message: str, **kwargs: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "state": state,
            "message": message,
            "updated_at": datetime.now(UTC).isoformat(),
            "languages": ["java"],
        }
        payload.update({key: value for key, value in kwargs.items() if value is not None})
        return payload

    def _safe_repository_id(self, repository_id: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(repository_id or "").strip())
