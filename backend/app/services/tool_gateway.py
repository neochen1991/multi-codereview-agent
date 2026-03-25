from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import re
from typing import Any

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.tool_plugin_loader import ToolPluginLoader
from app.services.capability_gateway import CapabilityGateway
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.postgres_metadata_service import PostgresMetadataService
from app.services.repository_context_service import RepositoryContextService


class ReviewToolGateway:
    """专家运行时工具网关。

    这里的 tool 指“代码审查运行时的可执行能力”，例如知识检索、diff 片段提取、
    测试面定位和代码仓上下文检索。专家只有在全局 allowlist 和自身绑定都允许时，
    才能真正调用这些工具。
    """

    TEST_PATH_MARKERS = {
        "test",
        "tests",
        "__tests__",
        "__mocks__",
        "spec",
        "specs",
        "fixtures",
        "playwright",
        "cypress",
    }

    def __init__(self, root: Path) -> None:
        self._gateway = CapabilityGateway()
        self._knowledge_retrieval = KnowledgeRetrievalService(root)
        self._diff_excerpt = DiffExcerptService()
        self._postgres_metadata = PostgresMetadataService()
        self._plugin_loader = ToolPluginLoader(Path(__file__).resolve().parents[3] / "extensions" / "tools")
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
        design_docs: list[dict[str, Any]] | None = None,
        extra_tools: list[str] | None = None,
        active_skills: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按专家白名单执行本轮允许的运行时工具。"""
        runtime_allowlist = set(runtime.runtime_tool_allowlist)
        allowed_tools = [
            tool_name
            for tool_name in expert.runtime_tool_bindings
            if not runtime_allowlist or tool_name in runtime_allowlist
        ][: expert.max_tool_calls]
        if (
            self._repo_context_enabled(runtime)
            and (not runtime_allowlist or "repo_context_search" in runtime_allowlist)
            and "repo_context_search" not in allowed_tools
        ):
            allowed_tools.append("repo_context_search")
        if (
            expert.expert_id == "database_analysis"
            and (not runtime_allowlist or "pg_schema_context" in runtime_allowlist)
            and "pg_schema_context" not in allowed_tools
        ):
            allowed_tools.append("pg_schema_context")
        for tool_name in extra_tools or []:
            if tool_name not in allowed_tools:
                allowed_tools.append(tool_name)
        results: list[dict[str, Any]] = []
        for tool_name in allowed_tools:
            if tool_name == "design_spec_alignment" and not list(design_docs or []):
                results.append(
                    {
                        "tool_name": tool_name,
                        "summary": "当前未上传详细设计文档，已跳过设计一致性检查。",
                        "success": False,
                        "skipped": True,
                        "skip_reason": "design_docs_missing",
                    }
                )
                continue
            payload = {
                "expert": expert.model_dump(mode="json"),
                "subject": subject.model_dump(mode="json"),
                "runtime": runtime.model_dump(mode="json"),
                "file_path": file_path,
                "line_start": line_start,
                "related_files": list(related_files or []),
                "design_docs": list(design_docs or []),
                "active_skills": list(active_skills or []),
            }
            try:
                output = self._gateway.invoke_binding(tool_name, payload)
            except KeyError:
                output = self._invoke_plugin_tool(tool_name, payload)
                if output is None:
                    continue
            merged_output = {"tool_name": tool_name, **output}
            merged_output.setdefault("success", True)
            results.append(merged_output)
        return results

    def _register_defaults(self) -> None:
        """注册项目内置的运行时工具。"""
        self._gateway.register("knowledge_search", "tool", self._knowledge_search)
        self._gateway.register("diff_inspector", "tool", self._diff_inspector)
        self._gateway.register("test_surface_locator", "tool", self._test_surface_locator)
        self._gateway.register("dependency_surface_locator", "tool", self._dependency_surface_locator)
        self._gateway.register("repo_context_search", "tool", self._repo_context_search)
        self._gateway.register("pg_schema_context", "tool", self._pg_schema_context)

    def _invoke_plugin_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        plugin = self._plugin_loader.get(tool_name)
        if plugin is None or plugin.runtime != "python":
            return None
        expert_payload = dict(payload.get("expert") or {})
        expert_id = str(expert_payload.get("expert_id") or "").strip()
        if plugin.allowed_experts and expert_id not in plugin.allowed_experts:
            return {
                "summary": f"{tool_name} 未绑定到当前专家 {expert_id}，已跳过。",
                "success": False,
                "skipped": True,
                "skip_reason": "expert_not_bound",
            }
        active_skill_ids = {
            str(item).strip()
            for item in list(payload.get("active_skills") or [])
            if str(item).strip()
        }
        if plugin.bound_skills and not active_skill_ids.intersection(plugin.bound_skills):
            return {
                "summary": f"{tool_name} 未命中已激活 skill，已跳过。",
                "success": False,
                "skipped": True,
                "skip_reason": "skill_not_bound",
                "bound_skills": list(plugin.bound_skills),
                "active_skills": sorted(active_skill_ids),
            }
        entry = Path(plugin.tool_path) / plugin.entry
        if not entry.exists():
            return None
        repo_root = Path(__file__).resolve().parents[3]
        backend_root = repo_root / "backend"
        env = dict(os.environ)
        pythonpath_items = [
            str(backend_root),
            str(repo_root),
            *(env.get("PYTHONPATH", "").split(os.pathsep) if env.get("PYTHONPATH") else []),
        ]
        env["PYTHONPATH"] = os.pathsep.join(item for item in pythonpath_items if item)
        try:
            completed = subprocess.run(
                [sys.executable, str(entry)],
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                check=False,
                timeout=plugin.timeout_seconds,
                cwd=str(repo_root),
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {
                "summary": f"{tool_name} 执行超时，已跳过该工具结果。",
                "success": False,
                "timed_out": True,
            }
        if completed.returncode != 0:
            return {
                "summary": completed.stderr.strip() or f"{tool_name} 执行失败",
                "success": False,
            }
        parsed = json.loads(completed.stdout or "{}")
        if isinstance(parsed, dict):
            parsed.setdefault("success", True)
            return parsed
        return {
            "summary": f"{tool_name} 返回了非对象结果",
            "success": False,
        }

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
        """从目标分支源码仓补充文件、符号和引用上下文。"""
        runtime = RuntimeSettings.model_validate(dict(payload.get("runtime") or {}))
        subject = ReviewSubject.model_validate(dict(payload.get("subject") or {}))
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
            if (
                str(item).strip()
                and str(item).strip() != file_path
                and self._is_source_like_path(str(item).strip())
            )
        ]
        if not service.is_ready():
            return {
                "summary": "代码仓上下文未配置或本地仓不可用",
                "primary_context": {},
                "related_contexts": [],
                "context_files": [],
                "matches": [],
        }
        primary_context = service.load_file_context(file_path, line_start, radius=20)
        excerpt = self._diff_excerpt.extract_excerpt(subject.unified_diff, file_path, line_start)
        symbol_queries, keyword_sources = self._extract_repo_search_terms(
            excerpt,
            primary_context=primary_context,
            file_path=file_path,
        )
        symbol_contexts = [
            self._filter_symbol_context(
                service.search_symbol_context(symbol, globs=None, definition_limit=2, reference_limit=4)
            )
            for symbol in symbol_queries[:3]
        ]
        related_source_snippets = self._build_related_source_snippets(
            service,
            symbol_contexts,
            primary_file_path=file_path,
        )
        compact_symbol_contexts = self._compact_symbol_contexts(symbol_contexts)
        related_contexts = [
            service.load_file_context(related_path, 1, radius=8)
            for related_path in related_files[:3]
        ]
        context_files = [
            item
            for item in [file_path, *related_files[:3]]
            if item and self._is_source_like_path(item)
        ]
        definition_hits = self._flatten_symbol_hits(compact_symbol_contexts, "definitions")
        reference_hits = self._flatten_symbol_hits(compact_symbol_contexts, "references")
        search_matches = self._build_symbol_matches(compact_symbol_contexts)
        search_commands = [self._build_repo_search_command(symbol) for symbol in symbol_queries[:3]]
        if not symbol_queries:
            summary = "未从目标 diff hunk、源码上下文或文件名中提取到方法名或类名，已跳过源码仓检索。"
        else:
            summary = (
                f"已按 {len(symbol_queries[:3])} 个方法/类关键词检索目标分支代码仓，"
                f"命中 {len(definition_hits)} 个定义文件、{len(reference_hits)} 个引用文件，"
                "并已自动排除 test/spec 与编译产物等噪音文件。"
            )
        return {
            "summary": summary,
            "primary_context": primary_context,
            "related_contexts": related_contexts,
            "context_files": context_files,
            "matches": search_matches[:8],
            "symbol_contexts": compact_symbol_contexts,
            "search_keywords": symbol_queries[:3],
            "search_keyword_sources": keyword_sources[:3],
            "search_commands": search_commands,
            "definition_hits": definition_hits[:10],
            "reference_hits": reference_hits[:10],
            "related_source_snippets": related_source_snippets[:4],
            "symbol_match_strategy": "文本检索命中 + 轻量定义特征判断",
            "symbol_match_explanation": (
                "先在目标分支源码仓里搜索 symbol 文本命中，再根据 function/class/const/export 等定义特征，"
                "把结果拆成 definitions 和 references。当前不是 AST 级静态分析，展示仅保留命中的源码文件名，不再展开命中行与 snippet。"
            ),
        }

    def _pg_schema_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = RuntimeSettings.model_validate(dict(payload.get("runtime") or {}))
        subject = ReviewSubject.model_validate(dict(payload.get("subject") or {}))
        file_path = str(payload.get("file_path") or "")
        line_start = int(payload.get("line_start") or 1)
        excerpt = self._diff_excerpt.extract_excerpt(str(subject.unified_diff or ""), file_path, line_start)
        expert = dict(payload.get("expert") or {})
        query_terms = [file_path, *[str(item) for item in list(expert.get("focus_areas") or [])]]
        context = self._postgres_metadata.collect_context(
            runtime,
            subject,
            file_path=file_path,
            diff_excerpt=excerpt,
            query_terms=query_terms,
        )
        output = context.to_payload()
        output["success"] = context.matched
        return output

    def _repo_context_enabled(self, runtime: RuntimeSettings) -> bool:
        return bool(runtime.code_repo_clone_url and runtime.code_repo_local_path)

    def _extract_repo_search_terms(
        self,
        excerpt: str,
        *,
        primary_context: dict[str, Any] | None = None,
        file_path: str = "",
    ) -> tuple[list[str], list[dict[str, str]]]:
        tokens: list[str] = []
        token_sources: list[dict[str, str]] = []
        cleaned_lines = [
            re.sub(r"^\s*\d+\s+\|\s*[+\- ]?", "", line).strip()
            for line in str(excerpt or "").splitlines()
        ]
        cleaned_excerpt = "\n".join(line for line in cleaned_lines if line)
        for token in self._extract_symbol_candidates(cleaned_excerpt):
            self._append_keyword_source(
                tokens,
                token_sources,
                token,
                source="diff_hunk",
                source_label="diff hunk",
            )
        context_snippet = self._clean_repo_search_text(str((primary_context or {}).get("snippet") or ""))
        for token in self._extract_symbol_candidates(context_snippet):
            self._append_keyword_source(
                tokens,
                token_sources,
                token,
                source="source_context",
                source_label="源码上下文",
            )
        file_stem_token = self._extract_symbol_from_file_name(file_path, allow_when_tokens_exist=not not tokens)
        self._append_keyword_source(
            tokens,
            token_sources,
            file_stem_token,
            source="file_name",
            source_label="文件名类名兜底",
        )
        return tokens[:4], token_sources[:4]

    def _extract_symbol_candidates(self, text: str) -> list[str]:
        if not text.strip():
            return []
        patterns = [
            r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\b(?:interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(",
            r"\b(?:public|protected|private|static|final|abstract|synchronized|default|native|strictfp)\s+"
            r"(?:<[^>{}\n]+>\s+)?(?:[A-Za-z_][A-Za-z0-9_<>,.?\[\]]*\s+)+([A-Za-z_][A-Za-z0-9_]*)"
            r"\s*\([^;\n{}]*\)\s*(?:throws\s+[A-Za-z0-9_, .<>]+)?\{?",
            r"\b(?:[A-Za-z_][A-Za-z0-9_<>,.?\[\]]+\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;\n{}]*\)\s*(?:throws\s+[A-Za-z0-9_, .<>]+)?\{",
            r"\b(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^)\n]*\)\s*\{",
        ]
        tokens: list[str] = []
        for pattern in patterns:
            for match in re.findall(pattern, text):
                token = str(match).strip()
                if not token:
                    continue
                if token.lower() in {
                    "if",
                    "for",
                    "while",
                    "switch",
                    "return",
                    "catch",
                    "new",
                    "throw",
                    "else",
                    "case",
                    "try",
                }:
                    continue
                if token not in tokens:
                    tokens.append(token)
        return tokens[:6]

    def _clean_repo_search_text(self, text: str) -> str:
        lines = [
            re.sub(r"^\s*\d+\s+\|\s*", "", line).strip()
            for line in str(text or "").splitlines()
        ]
        return "\n".join(line for line in lines if line)

    def _extract_symbol_from_file_name(self, file_path: str, *, allow_when_tokens_exist: bool) -> str:
        path = Path(str(file_path or "").strip())
        stem = path.stem.strip()
        if not stem or self._is_test_like_path(path.as_posix()):
            return ""
        if allow_when_tokens_exist and path.suffix.lower() not in {".java", ".kt", ".kts", ".scala", ".groovy", ".cs"}:
            return ""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stem):
            return ""
        return stem

    def _append_keyword_source(
        self,
        tokens: list[str],
        token_sources: list[dict[str, str]],
        token: str,
        *,
        source: str,
        source_label: str,
    ) -> None:
        normalized = str(token or "").strip()
        if not normalized or normalized in tokens:
            return
        tokens.append(normalized)
        token_sources.append(
            {
                "keyword": normalized,
                "source": source,
                "source_label": source_label,
            }
        )

    def _is_test_like_path(self, path: str) -> bool:
        normalized_path = Path(str(path or "").replace("\\", "/"))
        parts = normalized_path.parts
        if any(str(part).lower() in self.TEST_PATH_MARKERS for part in parts):
            return True
        name = normalized_path.name
        stem = normalized_path.stem
        lower_name = name.lower()
        lower_stem = stem.lower()
        if any(token in lower_name for token in [".test.", ".tests.", ".spec.", ".specs.", ".it."]):
            return True
        if lower_stem in {"test", "tests", "spec", "specs"}:
            return True
        if any(lower_stem.endswith(suffix) for suffix in ("_test", "_tests", "_spec", "_specs", "-test", "-tests", "-spec", "-specs")):
            return True
        return bool(re.search(r"(Test|Tests|Spec|Specs|IT|ITCase)$", stem))

    def _is_source_like_path(self, path: str) -> bool:
        normalized = str(path or "").strip()
        if not normalized or self._is_test_like_path(normalized):
            return False
        suffix = Path(normalized).suffix.lower()
        if suffix in {".class", ".jar", ".war", ".ear", ".dll", ".so", ".dylib", ".exe", ".o", ".a", ".pyc", ".pyo"}:
            return False
        return True

    def _filter_symbol_context(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            **context,
            "definitions": [
                item
                for item in list(context.get("definitions") or [])
                if isinstance(item, dict) and self._is_source_like_path(str(item.get("path") or ""))
            ],
            "references": [
                item
                for item in list(context.get("references") or [])
                if isinstance(item, dict) and self._is_source_like_path(str(item.get("path") or ""))
            ],
        }

    def _compact_symbol_contexts(self, symbol_contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact_contexts: list[dict[str, Any]] = []
        for context in symbol_contexts:
            symbol = str(context.get("symbol") or "").strip()
            if not symbol:
                continue
            compact_contexts.append(
                {
                    "symbol": symbol,
                    "definitions": self._compact_symbol_entries(context, "definitions"),
                    "references": self._compact_symbol_entries(context, "references"),
                }
            )
        return compact_contexts

    def _compact_symbol_entries(self, context: dict[str, Any], key: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for item in list(context.get(key) or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path or not self._is_source_like_path(path) or path in seen_paths:
                continue
            entries.append(
                {
                    "path": path,
                    "line_start": int(item.get("line_number") or 1),
                    "snippet": str(item.get("snippet") or "").strip(),
                }
            )
            seen_paths.add(path)
        return entries

    def _collect_symbol_hit_files(self, context: dict[str, Any], key: str) -> list[str]:
        files: list[str] = []
        for item in list(context.get(key) or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path or not self._is_source_like_path(path) or path in files:
                continue
            files.append(path)
        return files

    def _build_related_source_snippets(
        self,
        service: RepositoryContextService,
        symbol_contexts: list[dict[str, Any]],
        *,
        primary_file_path: str,
        max_snippets: int = 4,
    ) -> list[dict[str, Any]]:
        snippets: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for context in symbol_contexts:
            symbol = str(context.get("symbol") or "").strip()
            for kind in ("definitions", "references"):
                for item in list(context.get(kind) or []):
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path") or "").strip()
                    if (
                        not path
                        or path == primary_file_path
                        or path in seen_paths
                        or not self._is_source_like_path(path)
                    ):
                        continue
                    line_number = int(item.get("line_number") or 1)
                    context_snippet = service.load_file_context(path, line_number, radius=10)
                    snippet = str(context_snippet.get("snippet") or "").strip()
                    if not snippet:
                        continue
                    snippets.append(
                        {
                            "path": path,
                            "symbol": symbol,
                            "kind": "definition" if kind == "definitions" else "reference",
                            "line_start": line_number,
                            "snippet": snippet,
                        }
                    )
                    seen_paths.add(path)
                    if len(snippets) >= max_snippets:
                        return snippets
        return snippets

    def _flatten_symbol_hits(self, symbol_contexts: list[dict[str, Any]], key: str) -> list[str]:
        hits: list[str] = []
        for context in symbol_contexts:
            for item in list(context.get(key) or []):
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                if not path or not self._is_source_like_path(path) or path in hits:
                    continue
                hits.append(path)
        return hits

    def _build_symbol_matches(self, symbol_contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for context in symbol_contexts:
            symbol = str(context.get("symbol") or "").strip()
            for kind in ("definitions", "references"):
                for item in list(context.get(kind) or []):
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path") or "").strip()
                    key = (symbol, kind, path)
                    if not path or key in seen:
                        continue
                    seen.add(key)
                    matches.append({"query": symbol, "kind": kind, "path": path})
        return matches

    def _build_repo_search_command(self, symbol: str) -> str:
        return (
            f"rg --line-number --no-heading --glob '!.git/**' --glob '!**/*test*/**' "
            f"--glob '!**/*spec*/**' --glob '!**/__tests__/**' --glob '!**/*.class' \"{symbol}\""
        )


__all__ = ["ReviewToolGateway"]
