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
        for tool_name in extra_tools or []:
            if tool_name not in allowed_tools:
                allowed_tools.append(tool_name)
        results: list[dict[str, Any]] = []
        for tool_name in allowed_tools:
            payload = {
                "expert": expert.model_dump(mode="json"),
                "subject": subject.model_dump(mode="json"),
                "runtime": runtime.model_dump(mode="json"),
                "file_path": file_path,
                "line_start": line_start,
                "related_files": list(related_files or []),
                "design_docs": list(design_docs or []),
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

    def _invoke_plugin_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        plugin = self._plugin_loader.get(tool_name)
        if plugin is None or plugin.runtime != "python":
            return None
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
                and not self._is_test_like_path(str(item).strip())
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
        primary_context = service.load_file_context(file_path, line_start, radius=10)
        excerpt = self._diff_excerpt.extract_excerpt(subject.unified_diff, file_path, line_start)
        symbol_queries = self._extract_repo_search_terms(excerpt)
        symbol_contexts = [
            self._filter_symbol_context(
                service.search_symbol_context(symbol, globs=None, definition_limit=2, reference_limit=4)
            )
            for symbol in symbol_queries[:3]
        ]
        related_contexts = [
            service.load_file_context(related_path, 1, radius=8)
            for related_path in related_files[:3]
        ]
        context_files = [
            item
            for item in [file_path, *related_files[:3]]
            if item and not self._is_test_like_path(item)
        ]
        definition_hits = self._flatten_symbol_hits(symbol_contexts, "definitions")
        reference_hits = self._flatten_symbol_hits(symbol_contexts, "references")
        search_matches = self._build_symbol_matches(symbol_contexts)
        search_commands = [self._build_repo_search_command(symbol) for symbol in symbol_queries[:3]]
        if not symbol_queries:
            summary = "未从目标 diff hunk 中提取到方法名或类名，已跳过源码仓检索。"
        else:
            summary = (
                f"已按 {len(symbol_queries[:3])} 个方法/类关键词检索目标分支代码仓，"
                f"命中 {len(definition_hits)} 条定义、{len(reference_hits)} 条引用，"
                "并已自动排除 test/spec 等测试代码。"
            )
        return {
            "summary": summary,
            "primary_context": primary_context,
            "related_contexts": related_contexts,
            "context_files": context_files,
            "matches": search_matches[:8],
            "symbol_contexts": symbol_contexts,
            "search_keywords": symbol_queries[:3],
            "search_commands": search_commands,
            "definition_hits": definition_hits[:6],
            "reference_hits": reference_hits[:6],
            "symbol_match_strategy": "文本检索命中 + 轻量定义特征判断",
            "symbol_match_explanation": (
                "先在目标分支源码仓里搜索 symbol 文本命中，再根据 function/class/const/export 等定义特征，"
                "把结果拆成 definitions 和 references。当前不是 AST 级静态分析。"
            ),
        }

    def _repo_context_enabled(self, runtime: RuntimeSettings) -> bool:
        return bool(runtime.code_repo_clone_url and runtime.code_repo_local_path)

    def _extract_repo_search_terms(self, excerpt: str) -> list[str]:
        cleaned_lines = [
            re.sub(r"^\s*\d+\s+\|\s*[+\- ]?", "", line).strip()
            for line in str(excerpt or "").splitlines()
        ]
        cleaned_excerpt = "\n".join(line for line in cleaned_lines if line)
        patterns = [
            r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(",
            r"\b(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^)\n]*\)\s*\{",
        ]
        tokens: list[str] = []
        for pattern in patterns:
            for match in re.findall(pattern, cleaned_excerpt):
                token = str(match).strip()
                if not token:
                    continue
                if token.lower() in {"if", "for", "while", "switch", "return", "catch"}:
                    continue
                if token not in tokens:
                    tokens.append(token)
        return tokens[:4]

    def _is_test_like_path(self, path: str) -> bool:
        parts = Path(str(path or "").replace("\\", "/")).parts
        return any(str(part).lower() in self.TEST_PATH_MARKERS for part in parts)

    def _filter_symbol_context(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            **context,
            "definitions": [
                item
                for item in list(context.get("definitions") or [])
                if isinstance(item, dict) and not self._is_test_like_path(str(item.get("path") or ""))
            ],
            "references": [
                item
                for item in list(context.get("references") or [])
                if isinstance(item, dict) and not self._is_test_like_path(str(item.get("path") or ""))
            ],
        }

    def _flatten_symbol_hits(self, symbol_contexts: list[dict[str, Any]], key: str) -> list[str]:
        hits: list[str] = []
        for context in symbol_contexts:
            symbol = str(context.get("symbol") or "").strip()
            for item in list(context.get(key) or []):
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                line_number = int(item.get("line_number") or 0)
                snippet = str(item.get("snippet") or "").strip()
                if not path:
                    continue
                hits.append(f"{symbol} -> {path}:{line_number} · {snippet}")
        return hits

    def _build_symbol_matches(self, symbol_contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for context in symbol_contexts:
            symbol = str(context.get("symbol") or "").strip()
            for kind in ("definitions", "references"):
                for item in list(context.get(kind) or []):
                    if not isinstance(item, dict):
                        continue
                    matches.append({"query": symbol, "kind": kind, **item})
        return matches

    def _build_repo_search_command(self, symbol: str) -> str:
        return (
            f"rg --line-number --no-heading --glob '!.git/**' --glob '!**/*test*/**' "
            f"--glob '!**/*spec*/**' --glob '!**/__tests__/**' \"{symbol}\""
        )


__all__ = ["ReviewToolGateway"]
