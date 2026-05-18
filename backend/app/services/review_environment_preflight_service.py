from __future__ import annotations

import re
from pathlib import Path

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings


def run_review_environment_preflight(
    subject: ReviewSubject,
    runtime_settings: RuntimeSettings,
) -> dict[str, object]:
    metadata = dict(subject.metadata or {})
    workspace = _workspace_path(subject, runtime_settings)
    normalized_changed_files = _normalized_changed_files(subject)
    degraded_context_reasons: list[str] = []
    path_resolution_failures: list[str] = []
    workspace_exists = bool(workspace and workspace.exists())
    if not workspace_exists:
        degraded_context_reasons.append("workspace_missing")
    else:
        for file_path in normalized_changed_files:
            if not (workspace / file_path).exists():
                path_resolution_failures.append(file_path)
        if path_resolution_failures:
            degraded_context_reasons.append("changed_file_resolution_failed")
    code_graph_db_path = str(metadata.get("code_graph_db_path") or "").strip()
    code_graph_db_exists = bool(code_graph_db_path and Path(code_graph_db_path).exists())
    if code_graph_db_path and not code_graph_db_exists:
        degraded_context_reasons.append("code_graph_db_missing")
    tree_sitter_graph_result = _normalize_graph_result(metadata.get("tree_sitter_graph_result"))
    gitnexus_graph_result = _normalize_graph_result(metadata.get("gitnexus_graph_result"))
    if tree_sitter_graph_result and not _graph_result_ready(tree_sitter_graph_result):
        degraded_context_reasons.append("tree_sitter_graph_degraded")
    if gitnexus_graph_result and not _graph_result_ready(gitnexus_graph_result):
        degraded_context_reasons.append("gitnexus_graph_degraded")
    status = "passed" if not degraded_context_reasons else "warning"
    return {
        "status": status,
        "workspace_path": str(workspace or ""),
        "workspace_exists": workspace_exists,
        "code_graph_db_path": code_graph_db_path,
        "code_graph_db_exists": code_graph_db_exists,
        "tree_sitter_graph_result": tree_sitter_graph_result,
        "gitnexus_graph_result": gitnexus_graph_result,
        "normalized_changed_files": normalized_changed_files,
        "path_resolution_failures": path_resolution_failures,
        "degraded_context_reasons": degraded_context_reasons,
    }


def _workspace_path(subject: ReviewSubject, runtime_settings: RuntimeSettings) -> Path | None:
    raw = str((subject.metadata or {}).get("workspace_repo_path") or runtime_settings.code_repo_local_path or "").strip()
    return Path(raw) if raw else None


def _normalized_changed_files(subject: ReviewSubject) -> list[str]:
    candidates = [str(item).strip() for item in list(subject.changed_files or []) if str(item).strip()]
    if not candidates and str(subject.unified_diff or "").strip():
        candidates = re.findall(r"^diff --git a/(.*?) b/", str(subject.unified_diff), flags=re.MULTILINE)
    normalized: list[str] = []
    for item in candidates:
        value = item.replace("\\", "/").lstrip("/")
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _normalize_graph_result(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if str(key).strip()}


def _graph_result_ready(value: dict[str, object]) -> bool:
    status = str(value.get("status") or value.get("state") or value.get("phase") or "").strip().lower()
    if status in {"ready", "passed", "success", "ok", "available"}:
        return True
    if bool(value.get("ready")) is True:
        return True
    return False
