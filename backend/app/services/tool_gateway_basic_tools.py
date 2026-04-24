from __future__ import annotations

from typing import Any


def knowledge_search(*, knowledge_retrieval, payload: dict[str, Any]) -> dict[str, Any]:
    expert = dict(payload.get("expert") or {})
    subject = dict(payload.get("subject") or {})
    documents = knowledge_retrieval.retrieve(
        str(expert.get("expert_id") or ""),
        {
            "changed_files": list(subject.get("changed_files") or []),
            "knowledge_sources": list(expert.get("knowledge_sources") or []),
            "query_terms": [
                str(payload.get("file_path") or ""),
                *list(expert.get("focus_areas") or []),
            ],
        },
    )
    return {
        "summary": f"匹配到 {len(documents)} 篇知识文档",
        "matches": [
            {
                "doc_id": item.doc_id,
                "title": item.title,
                "source_filename": item.source_filename,
                "snippet": item.content[:280],
            }
            for item in documents[:4]
        ],
    }


def diff_inspector(*, diff_excerpt, payload: dict[str, Any]) -> dict[str, Any]:
    subject = dict(payload.get("subject") or {})
    file_path = str(payload.get("file_path") or "")
    line_start = int(payload.get("line_start") or 1)
    excerpt = diff_excerpt.extract_excerpt(
        str(subject.get("unified_diff") or ""),
        file_path,
        line_start,
    )
    return {
        "summary": f"提取 {file_path}:{line_start} 的 diff 片段",
        "excerpt": excerpt,
    }


def test_surface_locator(*, payload: dict[str, Any]) -> dict[str, Any]:
    subject = dict(payload.get("subject") or {})
    changed_files = [str(item) for item in subject.get("changed_files", [])]
    matched = [
        item
        for item in changed_files
        if any(token in item.lower() for token in ["test", "spec", "jest", "vitest", "pytest", "playwright"])
    ]
    return {
        "summary": f"定位到 {len(matched)} 个测试相关文件",
        "matched_files": matched[:8],
    }


def dependency_surface_locator(*, payload: dict[str, Any]) -> dict[str, Any]:
    subject = dict(payload.get("subject") or {})
    changed_files = [str(item) for item in subject.get("changed_files", [])]
    matched = [
        item
        for item in changed_files
        if any(token in item.lower() for token in ["service", "repository", "api", "module", "client", "domain"])
    ]
    return {
        "summary": f"定位到 {len(matched)} 个依赖/边界相关文件",
        "matched_files": matched[:8],
    }
