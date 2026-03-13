from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.capability_gateway import CapabilityGateway
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.repository_context_service import RepositoryContextService


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
        related_files: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        runtime_allowlist = set(runtime.skill_allowlist)
        allowed_skills = [
            skill_name
            for skill_name in expert.skill_bindings
            if not runtime_allowlist or skill_name in runtime_allowlist
        ][: expert.max_tool_calls]
        if (
            self._repo_context_enabled(runtime)
            and (not runtime_allowlist or "repo_context_search" in runtime_allowlist)
            and "repo_context_search" not in allowed_skills
        ):
            allowed_skills.append("repo_context_search")
        results: list[dict[str, Any]] = []
        for skill_name in allowed_skills:
            try:
                output = self._gateway.invoke_binding(
                    skill_name,
                    {
                        "expert": expert.model_dump(mode="json"),
                        "subject": subject.model_dump(mode="json"),
                        "runtime": runtime.model_dump(mode="json"),
                        "file_path": file_path,
                        "line_start": line_start,
                        "related_files": list(related_files or []),
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
        self._gateway.register("repo_context_search", "skill", self._repo_context_search)

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

    def _repo_context_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = RuntimeSettings.model_validate(dict(payload.get("runtime") or {}))
        service = RepositoryContextService(
            clone_url=runtime.code_repo_clone_url,
            local_path=runtime.code_repo_local_path,
            default_branch=runtime.code_repo_default_branch or runtime.default_target_branch,
            access_token=runtime.code_repo_access_token,
            auto_sync=runtime.code_repo_auto_sync,
        )
        file_path = str(payload.get("file_path") or "")
        line_start = int(payload.get("line_start") or 1)
        related_files = [
            str(item).strip()
            for item in list(payload.get("related_files") or [])
            if str(item).strip() and str(item).strip() != file_path
        ]
        if not service.is_ready():
            return {
                "summary": "代码仓上下文未配置或本地仓不可用",
                "primary_context": {},
                "related_contexts": [],
                "context_files": [],
                "matches": [],
            }
        primary_context = service.load_file_context(file_path, line_start, radius=10)
        file_name = Path(file_path).name
        stem = Path(file_path).stem
        query = stem if stem else file_name
        matches = service.search(query=query, globs=None, limit=6)
        symbol_queries = self._derive_repo_symbols(file_path, primary_context.get("snippet", ""))
        symbol_contexts = [
            service.search_symbol_context(symbol, globs=None, definition_limit=2, reference_limit=4)
            for symbol in symbol_queries[:3]
        ]
        related_contexts = [
            service.load_file_context(related_path, 1, radius=8)
            for related_path in related_files[:3]
        ]
        context_files = [
            item
            for item in [file_path, *related_files[:3]]
            if item
        ]
        return {
            "summary": (
                f"已从目标分支代码仓补充 {len(context_files)} 个文件上下文，"
                f"检索到 {len(matches.get('matches', []))} 条关联命中，"
                f"补充了 {len(symbol_contexts)} 组定义/引用上下文"
            ),
            "primary_context": primary_context,
            "related_contexts": related_contexts,
            "context_files": context_files,
            "matches": matches.get("matches", []),
            "symbol_contexts": symbol_contexts,
        }

    def _repo_context_enabled(self, runtime: RuntimeSettings) -> bool:
        return bool(runtime.code_repo_clone_url and runtime.code_repo_local_path)

    def _derive_repo_symbols(self, file_path: str, snippet: object) -> list[str]:
        tokens: list[str] = []
        stem = Path(file_path).stem
        if stem:
            tokens.append(stem)
        for match in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", str(snippet or "")):
            lowered = match.lower()
            if lowered in {"const", "return", "true", "false", "null", "none", "export"}:
                continue
            if match not in tokens:
                tokens.append(match)
            if len(tokens) >= 5:
                break
        return tokens
