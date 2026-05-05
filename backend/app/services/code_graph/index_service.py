from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.services.code_graph.file_selector import CodeGraphFileSelector
from app.services.code_graph.storage import CodeGraphStorage

logger = logging.getLogger(__name__)


class CodeGraphIndexService:
    """Build and refresh a local code graph for a repository."""

    def __init__(
        self,
        *,
        repo_root: str | Path,
        storage: CodeGraphStorage | None = None,
        selector: Any | None = None,
        parser: Any | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser()
        self.storage = storage or CodeGraphStorage(self.repo_root / ".code-review-graph" / "graph.db")
        self.selector = selector or CodeGraphFileSelector(self.repo_root)
        self.parser = parser

    def full_build(self, *, repository_id: str = "", languages: list[str] | None = None) -> dict[str, object]:
        self.storage.initialize()
        selected_files = self.selector.select_files(languages=languages or ["java"])
        logger.info(
            "tree_sitter index full_build request repository_id=%s repo_root=%s languages=%s selected_file_count=%s graph_db_path=%s",
            repository_id,
            self.repo_root,
            ",".join(languages or ["java"]),
            len(selected_files),
            self.storage.db_path,
        )
        indexed_file_count = 0
        skipped_unchanged_file_count = 0
        failed_files: list[dict[str, str]] = []
        node_count = 0
        edge_count = 0
        for file_path in selected_files:
            normalized = str(file_path or "").strip().replace("\\", "/")
            if not normalized:
                continue
            current_hash = self._file_hash(normalized)
            if not current_hash:
                failed_files.append({"file_path": normalized, "reason": "文件不存在或不可读取"})
                continue
            previous = self.storage.file_state(normalized)
            if previous.get("file_hash") == current_hash:
                skipped_unchanged_file_count += 1
                continue
            parsed = self._parse_file(normalized)
            nodes = list(parsed.get("nodes") or [])
            edges = list(parsed.get("edges") or [])
            self.storage.replace_file_graph(
                file_path=normalized,
                file_hash=current_hash,
                nodes=nodes,
                edges=edges,
            )
            indexed_file_count += 1
            node_count += len(nodes)
            edge_count += len(edges)
        result = {
            "state": "ready" if not failed_files else "degraded",
            "repository_id": repository_id,
            "selected_file_count": len(selected_files),
            "indexed_file_count": indexed_file_count,
            "skipped_unchanged_file_count": skipped_unchanged_file_count,
            "failed_file_count": len(failed_files),
            "failed_files": failed_files[:20],
            "node_count": node_count,
            "edge_count": edge_count,
            "graph_db_path": str(self.storage.db_path),
        }
        logger.info("tree_sitter index full_build response repository_id=%s result=%s", repository_id, self._compact_json(result))
        return result

    def _parse_file(self, relative_path: str) -> dict[str, object]:
        if self.parser is None:
            return {"nodes": [], "edges": []}
        parse_file = getattr(self.parser, "parse_file", None)
        if not callable(parse_file):
            return {"nodes": [], "edges": []}
        try:
            parsed = parse_file(self.repo_root, relative_path)
        except Exception:
            logger.exception("tree_sitter parse failed repo_root=%s file_path=%s", self.repo_root, relative_path)
            return {"nodes": [], "edges": []}
        result = dict(parsed or {})
        logger.info(
            "tree_sitter parse file io request=%s response=%s",
            self._compact_json({"repo_root": str(self.repo_root), "file_path": relative_path}),
            self._compact_json(
                {
                    "node_count": len(list(result.get("nodes") or [])),
                    "edge_count": len(list(result.get("edges") or [])),
                }
            ),
        )
        return result

    def _file_hash(self, relative_path: str) -> str:
        target = self.repo_root / relative_path
        try:
            data = target.read_bytes()
        except OSError:
            return ""
        return hashlib.sha256(data).hexdigest()

    def _compact_json(self, value: object, *, max_chars: int = 6000) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}... [truncated {len(text) - max_chars} chars]"
