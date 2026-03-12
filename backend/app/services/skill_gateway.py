from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.capability_gateway import CapabilityGateway
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService


class SkillGateway:
    def __init__(self, root: Path) -> None:
        self._gateway = CapabilityGateway()
        self._knowledge_retrieval = KnowledgeRetrievalService(root)
        self._diff_excerpt = DiffExcerptService()
        self._register_defaults()

    def invoke_for_expert(
        self,
        expert: ExpertProfile,
        subject: ReviewSubject,
        runtime: RuntimeSettings,
        *,
        file_path: str,
        line_start: int,
    ) -> list[dict[str, Any]]:
        runtime_allowlist = set(runtime.skill_allowlist)
        allowed_skills = [
            skill_name
            for skill_name in expert.skill_bindings
            if not runtime_allowlist or skill_name in runtime_allowlist
        ][: expert.max_tool_calls]
        results: list[dict[str, Any]] = []
        for skill_name in allowed_skills:
            try:
                output = self._gateway.invoke_binding(
                    skill_name,
                    {
                        "expert": expert.model_dump(mode="json"),
                        "subject": subject.model_dump(mode="json"),
                        "file_path": file_path,
                        "line_start": line_start,
                    },
                )
            except KeyError:
                continue
            results.append(
                {
                    "skill_name": skill_name,
                    "success": True,
                    **output,
                }
            )
        return results

    def _register_defaults(self) -> None:
        self._gateway.register("knowledge_search", "skill", self._knowledge_search)
        self._gateway.register("diff_inspector", "skill", self._diff_inspector)
        self._gateway.register("test_surface_locator", "skill", self._test_surface_locator)
        self._gateway.register("dependency_surface_locator", "skill", self._dependency_surface_locator)

    def _knowledge_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        expert = dict(payload.get("expert") or {})
        subject = dict(payload.get("subject") or {})
        documents = self._knowledge_retrieval.retrieve(
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

    def _diff_inspector(self, payload: dict[str, Any]) -> dict[str, Any]:
        subject = dict(payload.get("subject") or {})
        file_path = str(payload.get("file_path") or "")
        line_start = int(payload.get("line_start") or 1)
        excerpt = self._diff_excerpt.extract_excerpt(
            str(subject.get("unified_diff") or ""),
            file_path,
            line_start,
        )
        return {
            "summary": f"提取 {file_path}:{line_start} 的 diff 片段",
            "excerpt": excerpt,
        }

    def _test_surface_locator(self, payload: dict[str, Any]) -> dict[str, Any]:
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

    def _dependency_surface_locator(self, payload: dict[str, Any]) -> dict[str, Any]:
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
