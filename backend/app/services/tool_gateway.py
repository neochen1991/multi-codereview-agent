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
from app.services.java_ddd_context_assembler import JavaDddContextAssembler
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
        self._java_ddd_context_assembler = JavaDddContextAssembler()
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
            self._repo_context_enabled(runtime, subject)
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
        for tool_name in self._java_ddd_runtime_tools_for_expert(expert.expert_id, file_path):
            if (not runtime_allowlist or tool_name in runtime_allowlist) and tool_name not in allowed_tools:
                allowed_tools.append(tool_name)
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
        self._gateway.register("transaction_boundary_inspector", "tool", self._transaction_boundary_inspector)
        self._gateway.register("aggregate_invariant_inspector", "tool", self._aggregate_invariant_inspector)
        self._gateway.register(
            "application_service_boundary_inspector",
            "tool",
            self._application_service_boundary_inspector,
        )
        self._gateway.register(
            "controller_entry_guard_inspector",
            "tool",
            self._controller_entry_guard_inspector,
        )
        self._gateway.register(
            "repository_query_risk_inspector",
            "tool",
            self._repository_query_risk_inspector,
        )

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
        service = RepositoryContextService.from_review_context(
            clone_url=runtime.code_repo_clone_url,
            local_path=runtime.code_repo_local_path,
            default_branch=runtime.code_repo_default_branch or runtime.default_target_branch,
            access_token=runtime.code_repo_access_token,
            auto_sync=runtime.code_repo_auto_sync,
            subject=subject,
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
        symbol_priorities = self._build_symbol_priority_map(keyword_sources)
        ranked_anchors = self._collect_ranked_symbol_anchors(symbol_contexts, symbol_priorities=symbol_priorities)
        derived_related_files = self._derive_related_files_from_anchors(
            ranked_anchors,
            primary_file_path=file_path,
        )
        effective_related_files = related_files or derived_related_files
        related_source_snippets = self._build_related_source_snippets(
            service,
            symbol_contexts,
            primary_file_path=file_path,
            related_files=effective_related_files,
            search_keyword_sources=keyword_sources,
        )
        compact_symbol_contexts = self._compact_symbol_contexts(symbol_contexts)
        related_contexts = [
            self._load_related_context(
                service,
                related_path,
                symbol_contexts=symbol_contexts,
                symbol_priorities=symbol_priorities,
            )
            for related_path in effective_related_files[:3]
        ]
        java_ddd_context: dict[str, object] = {}
        if Path(file_path).suffix.lower() == ".java":
            java_ddd_context = self._java_ddd_context_assembler.build_context_pack(
                service,
                file_path=file_path,
                line_start=line_start,
                primary_context=primary_context,
                related_files=related_files,
                symbol_contexts=symbol_contexts,
                excerpt=excerpt,
            )
        context_files = [
            item
            for item in [file_path, *effective_related_files[:3]]
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
                "把结果拆成 definitions 和 references。当前不是 AST 级静态分析，但会保留命中行和代码片段。"
            ),
            **java_ddd_context,
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

    def _transaction_boundary_inspector(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._build_repository_context_service(payload)
        file_path = str(payload.get("file_path") or "").strip()
        line_start = int(payload.get("line_start") or 1)
        if not service.is_ready() or not file_path:
            return {
                "summary": "事务边界核验跳过：代码仓上下文不可用。",
                "success": False,
                "signals": [],
                "call_chain": [],
            }
        current = service.load_file_context(file_path, line_start, radius=18)
        snippet = str(current.get("snippet") or "").strip()
        normalized = snippet.lower()
        signals: list[str] = []
        if "@transactional" in normalized:
            signals.append("transaction_annotation_present")
        if any(token in normalized for token in [".save(", ".update(", ".insert(", ".delete("]):
            signals.append("repository_write_detected")
        if any(token in normalized for token in [".publish", "domaineventpublisher", "kafkatemplate", "rabbittemplate"]):
            signals.append("message_publish_detected")
        if any(token in normalized for token in [".call(", "feign", "resttemplate", "webclient", "httpclient"]):
            signals.append("remote_call_detected")
        java_context = self._extract_java_ddd_context(payload)
        call_chain = self._build_transaction_call_chain(java_context)
        summary_parts: list[str] = []
        if "transaction_annotation_present" in signals:
            summary_parts.append("检测到事务边界")
        if "repository_write_detected" in signals:
            summary_parts.append("事务内存在仓储写入")
        if "message_publish_detected" in signals:
            summary_parts.append("事务内存在事件/消息发布")
        if "remote_call_detected" in signals:
            summary_parts.append("事务内存在远程调用信号")
        if call_chain:
            summary_parts.append(f"调用链: {' -> '.join(call_chain[:6])}")
        return {
            "summary": "；".join(summary_parts) or "未发现明显事务边界风险信号。",
            "success": True,
            "signals": signals,
            "call_chain": call_chain,
            "snippet": snippet,
        }

    def _aggregate_invariant_inspector(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._build_repository_context_service(payload)
        file_path = str(payload.get("file_path") or "").strip()
        line_start = int(payload.get("line_start") or 1)
        if not service.is_ready() or not file_path:
            return {
                "summary": "聚合不变量核验跳过：代码仓上下文不可用。",
                "success": False,
                "signals": [],
                "aggregate_symbols": [],
            }
        current = service.load_file_context(file_path, line_start, radius=18)
        snippet = str(current.get("snippet") or "").strip()
        normalized = snippet.lower()
        target_hunk = dict(payload.get("target_hunk") or {})
        diff_excerpt = str(target_hunk.get("excerpt") or "").strip()
        diff_lower = diff_excerpt.lower()
        java_context = self._extract_java_ddd_context(payload)
        domain_contexts = list(java_context.get("domain_model_contexts") or [])
        aggregate_symbols = [
            str(item.get("symbol") or "").strip()
            for item in domain_contexts
            if isinstance(item, dict) and str(item.get("symbol") or "").strip()
        ]
        if not aggregate_symbols:
            for symbol in re.findall(r"\bnew\s+([A-Z][A-Za-z0-9_]*)\s*\(", f"{snippet}\n{diff_excerpt}"):
                if symbol not in aggregate_symbols:
                    aggregate_symbols.append(symbol)
        signals: list[str] = []
        if "setstatus(" in normalized:
            signals.append("direct_state_mutation_detected")
        if any(token in normalized for token in [".set", ".change", ".assign"]) and aggregate_symbols:
            signals.append("aggregate_mutator_invoked")
        if not any(token in normalized for token in ["validate", "check", "ensure", "invariant"]) and aggregate_symbols:
            signals.append("invariant_guard_not_visible")
        if re.search(r"\bnew\s+[A-Z][A-Za-z0-9_]*\s*\(", f"{snippet}\n{diff_excerpt}"):
            signals.append("direct_constructor_invocation_detected")
        if ".pulldomainevents(" in normalized or ".pulldomainevents(" in diff_lower:
            signals.append("domain_event_pull_detected")
        if "course.create(" in diff_lower and "new course(" in diff_lower:
            signals.append("aggregate_factory_bypass_detected")
        if "direct_constructor_invocation_detected" in signals and "domain_event_pull_detected" in signals:
            signals.append("domain_event_publish_after_direct_construction")
        summary_parts: list[str] = []
        if aggregate_symbols:
            summary_parts.append(f"关联领域对象: {' / '.join(aggregate_symbols[:4])}")
        if "direct_state_mutation_detected" in signals:
            summary_parts.append("检测到直接状态修改")
        if "invariant_guard_not_visible" in signals:
            summary_parts.append("当前片段未看到显式不变量保护")
        if "aggregate_factory_bypass_detected" in signals:
            summary_parts.append("检测到聚合工厂/工厂方法被直接构造绕过")
        if "domain_event_publish_after_direct_construction" in signals:
            summary_parts.append("直接构造后仍发布 pullDomainEvents，需核验领域事件是否已正确记录")
        return {
            "summary": "；".join(summary_parts) or "未发现明显聚合不变量风险信号。",
            "success": True,
            "signals": signals,
            "aggregate_symbols": aggregate_symbols[:6],
            "snippet": snippet,
        }

    def _application_service_boundary_inspector(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._build_repository_context_service(payload)
        file_path = str(payload.get("file_path") or "").strip()
        line_start = int(payload.get("line_start") or 1)
        if not service.is_ready() or not file_path:
            return {
                "summary": "应用服务边界核验跳过：代码仓上下文不可用。",
                "success": False,
                "signals": [],
                "callee_symbols": [],
            }
        current = service.load_file_context(file_path, line_start, radius=18)
        snippet = str(current.get("snippet") or "").strip()
        normalized = snippet.lower()
        java_context = self._extract_java_ddd_context(payload)
        callee_contexts = list(java_context.get("callee_contexts") or [])
        callee_symbols = [
            str(item.get("symbol") or "").strip()
            for item in callee_contexts
            if isinstance(item, dict) and str(item.get("symbol") or "").strip()
        ]
        signals: list[str] = []
        lower_path = file_path.lower()
        if "application" in lower_path or "appservice" in lower_path:
            if any(token in normalized for token in [".save(", ".update(", ".insert(", ".delete("]):
                signals.append("application_service_direct_persistence")
            if any(token in normalized for token in [".publish", "domaineventpublisher"]):
                signals.append("application_service_event_orchestration")
            if any(token in normalized for token in [".set", ".change", ".assign"]):
                signals.append("application_service_mutates_domain_state")
        summary_parts: list[str] = []
        if callee_symbols:
            summary_parts.append(f"被调方: {' / '.join(callee_symbols[:5])}")
        if "application_service_direct_persistence" in signals:
            summary_parts.append("应用服务直接进行持久化写入")
        if "application_service_mutates_domain_state" in signals:
            summary_parts.append("应用服务直接修改领域状态")
        if "application_service_event_orchestration" in signals:
            summary_parts.append("应用服务内存在事件发布编排")
        return {
            "summary": "；".join(summary_parts) or "未发现明显应用服务边界风险信号。",
            "success": True,
            "signals": signals,
            "callee_symbols": callee_symbols[:8],
            "snippet": snippet,
        }

    def _controller_entry_guard_inspector(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._build_repository_context_service(payload)
        file_path = str(payload.get("file_path") or "").strip()
        line_start = int(payload.get("line_start") or 1)
        if not service.is_ready() or not file_path:
            return {
                "summary": "接口入口保护核验跳过：代码仓上下文不可用。",
                "success": False,
                "signals": [],
                "snippet": "",
            }
        current = service.load_file_context(file_path, line_start, radius=18)
        snippet = str(current.get("snippet") or "").strip()
        java_context = self._extract_java_ddd_context(payload)
        caller_contexts = list(java_context.get("caller_contexts") or [])
        controller_contexts = [
            item for item in caller_contexts if isinstance(item, dict) and str(item.get("caller_type") or "").strip() == "controller"
        ]
        controller_snippets = [str(item.get("snippet") or "").strip() for item in controller_contexts if str(item.get("snippet") or "").strip()]
        controller_blob = "\n".join(controller_snippets[:2]) or snippet
        normalized = controller_blob.lower()
        signals: list[str] = []
        if "@restcontroller" in normalized or "@controller" in normalized or controller_contexts:
            signals.append("controller_entry_detected")
        if any(token in normalized for token in ["@validated", "@valid", "bindingresult", "validator", ".validate("]):
            signals.append("input_validation_present")
        else:
            signals.append("input_validation_not_visible")
        if any(token in normalized for token in ["@preauthorize", "@secured", "@rolesallowed", "permission", "tenant", "auth"]):
            signals.append("auth_or_tenant_guard_present")
        summary_parts: list[str] = []
        if controller_contexts:
            summary_parts.append(f"关联 Controller 数量: {len(controller_contexts[:3])}")
        if "input_validation_present" in signals:
            summary_parts.append("接口入口可见参数校验信号")
        if "input_validation_not_visible" in signals:
            summary_parts.append("接口入口未见明显参数校验信号")
        if "auth_or_tenant_guard_present" in signals:
            summary_parts.append("接口入口可见权限/租户保护信号")
        return {
            "summary": "；".join(summary_parts) or "未发现明显接口入口保护信号。",
            "success": True,
            "signals": signals,
            "snippet": controller_blob,
        }

    def _repository_query_risk_inspector(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._build_repository_context_service(payload)
        file_path = str(payload.get("file_path") or "").strip()
        line_start = int(payload.get("line_start") or 1)
        if not service.is_ready() or not file_path:
            return {
                "summary": "Repository/查询风险核验跳过：代码仓上下文不可用。",
                "success": False,
                "signals": [],
                "snippet": "",
            }
        current = service.load_file_context(file_path, line_start, radius=18)
        snippet = str(current.get("snippet") or "").strip()
        java_context = self._extract_java_ddd_context(payload)
        persistence_contexts = [item for item in list(java_context.get("persistence_contexts") or []) if isinstance(item, dict)]
        callee_contexts = [item for item in list(java_context.get("callee_contexts") or []) if isinstance(item, dict)]
        persistence_blob = "\n".join(
            [
                snippet,
                *[str(item.get("snippet") or "").strip() for item in persistence_contexts[:3] if str(item.get("snippet") or "").strip()],
                *[str(item.get("snippet") or "").strip() for item in callee_contexts[:2] if str(item.get("snippet") or "").strip()],
            ]
        )
        normalized = persistence_blob.lower()
        signals: list[str] = []
        if any(token in normalized for token in ["findbystatus", "select", "from", "where"]):
            signals.append("query_surface_detected")
        if "findbystatus" in normalized or ("status" in normalized and "list<" in normalized):
            signals.append("status_query_detected")
        if "like" in normalized:
            signals.append("like_query_detected")
        if "limit" not in normalized and "page" not in normalized and "pagesize" not in normalized and "pagination" not in normalized:
            signals.append("pagination_not_visible")
        summary_parts: list[str] = []
        if persistence_contexts:
            summary_parts.append(f"持久化上下文: {len(persistence_contexts[:3])} 个片段")
        if "status_query_detected" in signals:
            summary_parts.append("检测到按状态或条件批量查询信号")
        if "like_query_detected" in signals:
            summary_parts.append("检测到模糊查询信号")
        if "pagination_not_visible" in signals and "query_surface_detected" in signals:
            summary_parts.append("当前查询片段未见明显分页/limit 保护")
        return {
            "summary": "；".join(summary_parts) or "未发现明显 Repository/查询风险信号。",
            "success": True,
            "signals": signals,
            "snippet": persistence_blob,
        }

    def _repo_context_enabled(self, runtime: RuntimeSettings, subject: ReviewSubject) -> bool:
        service = RepositoryContextService.from_review_context(
            clone_url=runtime.code_repo_clone_url,
            local_path=runtime.code_repo_local_path,
            default_branch=runtime.code_repo_default_branch or runtime.default_target_branch,
            access_token=runtime.code_repo_access_token,
            auto_sync=runtime.code_repo_auto_sync,
            subject=subject,
        )
        return service.is_ready()

    def _build_repository_context_service(self, payload: dict[str, Any]) -> RepositoryContextService:
        runtime = RuntimeSettings.model_validate(dict(payload.get("runtime") or {}))
        subject = dict(payload.get("subject") or {})
        return RepositoryContextService.from_review_context(
            clone_url=runtime.code_repo_clone_url,
            local_path=runtime.code_repo_local_path,
            default_branch=runtime.code_repo_default_branch or runtime.default_target_branch,
            access_token=runtime.code_repo_access_token,
            auto_sync=runtime.code_repo_auto_sync,
            subject=subject,
        )

    def _extract_java_ddd_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._build_repository_context_service(payload)
        file_path = str(payload.get("file_path") or "").strip()
        line_start = int(payload.get("line_start") or 1)
        related_files = [
            str(item).strip()
            for item in list(payload.get("related_files") or [])
            if str(item).strip() and str(item).strip() != file_path
        ]
        subject = ReviewSubject.model_validate(dict(payload.get("subject") or {}))
        excerpt = self._diff_excerpt.extract_excerpt(str(subject.unified_diff or ""), file_path, line_start)
        primary_context = service.load_file_context(file_path, line_start, radius=20)
        symbol_queries, _ = self._extract_repo_search_terms(
            excerpt,
            primary_context=primary_context,
            file_path=file_path,
        )
        symbol_contexts = [
            self._filter_symbol_context(
                service.search_symbol_context(symbol, globs=["*.java", "*.xml"], definition_limit=2, reference_limit=4)
            )
            for symbol in symbol_queries[:3]
        ]
        if Path(file_path).suffix.lower() != ".java":
            return {}
        return self._java_ddd_context_assembler.build_context_pack(
            service,
            file_path=file_path,
            line_start=line_start,
            primary_context=primary_context,
            related_files=related_files,
            symbol_contexts=symbol_contexts,
            excerpt=excerpt,
        )

    def _build_transaction_call_chain(self, java_context: dict[str, Any]) -> list[str]:
        chain: list[str] = []
        current = dict(java_context.get("current_class_context") or {})
        if current:
            class_name = str(current.get("class_name") or current.get("path") or "").strip()
            if class_name:
                chain.append(f"current:{class_name}")
        for key, prefix in [("caller_contexts", "caller"), ("callee_contexts", "callee")]:
            for item in list(java_context.get(key) or []):
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or item.get("path") or "").strip()
                entry = f"{prefix}:{symbol}" if symbol else ""
                if entry and entry not in chain:
                    chain.append(entry)
        return chain[:8]

    def _java_ddd_runtime_tools_for_expert(self, expert_id: str, file_path: str) -> list[str]:
        if Path(str(file_path or "")).suffix.lower() != ".java":
            return []
        mapping = {
            "security_compliance": ["controller_entry_guard_inspector", "application_service_boundary_inspector", "transaction_boundary_inspector"],
            "performance_reliability": ["transaction_boundary_inspector", "repository_query_risk_inspector"],
            "database_analysis": ["repository_query_risk_inspector"],
            "architecture_design": ["application_service_boundary_inspector", "aggregate_invariant_inspector"],
            "ddd_specification": ["aggregate_invariant_inspector", "transaction_boundary_inspector"],
        }
        return list(mapping.get(expert_id, []))

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
        related_files: list[str] | None = None,
        search_keyword_sources: list[dict[str, str]] | None = None,
        max_snippets: int = 4,
    ) -> list[dict[str, Any]]:
        snippets: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        symbol_priorities = self._build_symbol_priority_map(search_keyword_sources or [])
        ranked_anchors = self._collect_ranked_symbol_anchors(symbol_contexts, symbol_priorities=symbol_priorities)
        best_anchor_by_path: dict[str, dict[str, Any]] = {}
        for anchor in ranked_anchors:
            path = str(anchor.get("path") or "").strip()
            if (
                not path
                or path == primary_file_path
                or path in best_anchor_by_path
                or not self._is_source_like_path(path)
            ):
                continue
            best_anchor_by_path[path] = anchor
        for anchor in best_anchor_by_path.values():
            path = str(anchor.get("path") or "").strip()
            if (
                not path
                or path == primary_file_path
                or path in seen_paths
                or not self._is_source_like_path(path)
            ):
                continue
            line_number = int(anchor.get("line_number") or 1)
            context_snippet = self._load_related_context_window(service, path, line_number)
            snippet = str(context_snippet.get("snippet") or "").strip()
            if not snippet:
                continue
            snippets.append(
                {
                    "path": path,
                    "symbol": str(anchor.get("symbol") or "").strip(),
                    "kind": str(anchor.get("kind") or "reference").strip(),
                    "line_start": line_number,
                    "snippet": snippet,
                }
            )
            seen_paths.add(path)
            if len(snippets) >= max_snippets:
                return snippets
        for related_path in list(related_files or []):
            normalized_path = str(related_path or "").strip()
            if (
                not normalized_path
                or normalized_path == primary_file_path
                or normalized_path in seen_paths
                or not self._is_source_like_path(normalized_path)
            ):
                continue
            line_number = self._pick_related_context_line(
                service,
                normalized_path,
                symbol_contexts,
                symbol_priorities=symbol_priorities,
            )
            context_snippet = self._load_related_context_window(service, normalized_path, line_number)
            snippet = str(context_snippet.get("snippet") or "").strip()
            if not snippet:
                continue
            snippets.append(
                {
                    "path": normalized_path,
                    "symbol": Path(normalized_path).stem,
                    "kind": "reference",
                    "line_start": line_number,
                    "snippet": snippet,
                }
            )
            seen_paths.add(normalized_path)
            if len(snippets) >= max_snippets:
                return snippets
        return snippets

    def _derive_related_files_from_anchors(
        self,
        ranked_anchors: list[dict[str, Any]],
        *,
        primary_file_path: str,
        max_files: int = 3,
    ) -> list[str]:
        related_files: list[str] = []
        seen_paths: set[str] = set()
        for anchor in ranked_anchors:
            path = str(anchor.get("path") or "").strip()
            if (
                not path
                or path == primary_file_path
                or path in seen_paths
                or not self._is_source_like_path(path)
            ):
                continue
            seen_paths.add(path)
            related_files.append(path)
            if len(related_files) >= max_files:
                break
        return related_files

    def _load_related_context(
        self,
        service: RepositoryContextService,
        related_path: str,
        *,
        symbol_contexts: list[dict[str, Any]],
        symbol_priorities: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        line_number = self._pick_related_context_line(
            service,
            related_path,
            symbol_contexts,
            symbol_priorities=symbol_priorities,
        )
        return self._load_related_context_window(service, related_path, line_number)

    def _load_related_context_window(
        self,
        service: RepositoryContextService,
        related_path: str,
        line_number: int,
    ) -> dict[str, Any]:
        normalized_line = max(1, int(line_number or 1))
        target = (service.local_path / related_path).resolve()
        if not target.exists() or not target.is_file():
            return service.load_file_range(related_path, normalized_line, normalized_line, padding=3)
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        start = max(0, normalized_line - 3)
        end = min(len(lines), normalized_line + 6)
        snippet = "\n".join(f"{index + 1:>4} | {lines[index]}" for index in range(start, end))
        return {
            "path": related_path,
            "snippet": snippet,
            "line_start": normalized_line,
            "line_end": normalized_line,
        }

    def _pick_related_context_line(
        self,
        service: RepositoryContextService,
        related_path: str,
        symbol_contexts: list[dict[str, Any]],
        *,
        symbol_priorities: dict[str, int] | None = None,
    ) -> int:
        normalized_path = str(related_path or "").strip()
        best_anchor = self._select_best_anchor_for_path(
            normalized_path,
            symbol_contexts,
            symbol_priorities=symbol_priorities or {},
        )
        if best_anchor:
            return max(1, int(best_anchor.get("line_number") or best_anchor.get("line_start") or 1))
        file_symbol = Path(normalized_path).stem.strip()
        if file_symbol:
            symbol_context = self._filter_symbol_context(
                service.search_symbol_context(file_symbol, globs=[f"*{Path(normalized_path).suffix}"], definition_limit=1, reference_limit=0)
            )
            fallback_anchor = self._select_best_anchor_for_path(
                normalized_path,
                [symbol_context],
                symbol_priorities=symbol_priorities or {},
            )
            if fallback_anchor:
                return max(1, int(fallback_anchor.get("line_number") or fallback_anchor.get("line_start") or 1))
        return self._first_meaningful_code_line(service, normalized_path)

    def _collect_ranked_symbol_anchors(
        self,
        symbol_contexts: list[dict[str, Any]],
        *,
        symbol_priorities: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        anchors: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int, str]] = set()
        priorities = dict(symbol_priorities or {})
        for context in symbol_contexts:
            symbol = str(context.get("symbol") or "").strip()
            for raw_kind in ("definitions", "references"):
                kind = "definition" if raw_kind == "definitions" else "reference"
                for item in list(context.get(raw_kind) or []):
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path") or "").strip()
                    line_number = int(item.get("line_number") or item.get("line_start") or 1)
                    snippet = str(item.get("snippet") or "").strip()
                    if not path or not self._is_source_like_path(path):
                        continue
                    key = (path, kind, line_number, symbol)
                    if key in seen:
                        continue
                    seen.add(key)
                    score = self._score_symbol_anchor(
                        symbol=symbol,
                        kind=kind,
                        path=path,
                        line_number=line_number,
                        snippet=snippet,
                        symbol_priority=priorities.get(symbol, 0),
                    )
                    anchors.append(
                        {
                            "symbol": symbol,
                            "kind": kind,
                            "path": path,
                            "line_number": line_number,
                            "snippet": snippet,
                            "score": score,
                        }
                    )
        anchors.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                str(item.get("path") or ""),
                int(item.get("line_number") or 1),
            )
        )
        return anchors

    def _select_best_anchor_for_path(
        self,
        target_path: str,
        symbol_contexts: list[dict[str, Any]],
        *,
        symbol_priorities: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        normalized_path = str(target_path or "").strip()
        if not normalized_path:
            return None
        anchors = [
            item
            for item in self._collect_ranked_symbol_anchors(
                symbol_contexts,
                symbol_priorities=symbol_priorities or {},
            )
            if str(item.get("path") or "").strip() == normalized_path
        ]
        return anchors[0] if anchors else None

    def _score_symbol_anchor(
        self,
        *,
        symbol: str,
        kind: str,
        path: str,
        line_number: int,
        snippet: str,
        symbol_priority: int = 0,
    ) -> int:
        score = symbol_priority * 10
        normalized_kind = str(kind or "").strip()
        if normalized_kind == "definition":
            score += 40
        if self._looks_like_java_signature(snippet, symbol=symbol):
            score += 35
        if self._looks_like_java_call_site(snippet, symbol=symbol):
            score += 20
        if self._looks_like_low_value_context(snippet):
            score -= 60
        if Path(path).suffix.lower() == ".java":
            score += 5
        if symbol_priority > 0:
            score += 25
        if line_number <= 5:
            score -= 10
        return score

    def _build_symbol_priority_map(self, keyword_sources: list[dict[str, str]]) -> dict[str, int]:
        priorities: dict[str, int] = {}
        for index, item in enumerate(keyword_sources):
            if not isinstance(item, dict):
                continue
            keyword = str(item.get("keyword") or "").strip()
            source = str(item.get("source") or "").strip()
            if not keyword:
                continue
            if source == "diff_hunk":
                base = 3
            elif source == "source_context":
                base = 2
            elif source == "file_name":
                base = 1
            else:
                base = 0
            priorities.setdefault(keyword, max(base - min(index, 2), 0))
        return priorities

    def _looks_like_java_signature(self, snippet: str, *, symbol: str) -> bool:
        normalized = str(snippet or "").strip()
        if not normalized:
            return False
        escaped_symbol = re.escape(str(symbol or "").strip())
        patterns = [
            rf"\b(?:public|protected|private|static|final|abstract|sealed|non-sealed)?\s*(?:class|interface|enum|record)\s+{escaped_symbol}\b",
            rf"\b{escaped_symbol}\s*\(",
            rf"\b(?:public|protected|private|static|final|default|abstract|synchronized|native|strictfp)\b.*\b{escaped_symbol}\s*\(",
        ]
        return any(re.search(pattern, normalized) for pattern in patterns)

    def _looks_like_java_call_site(self, snippet: str, *, symbol: str) -> bool:
        normalized = str(snippet or "").strip()
        if not normalized:
            return False
        escaped_symbol = re.escape(str(symbol or "").strip())
        return bool(
            re.search(rf"(?:\.|\b){escaped_symbol}\s*\(", normalized)
            or re.search(rf"\bnew\s+{escaped_symbol}\s*\(", normalized)
        )

    def _looks_like_low_value_context(self, snippet: str) -> bool:
        normalized = str(snippet or "").strip()
        if not normalized:
            return True
        lowered = normalized.lower()
        if lowered.startswith("package ") or lowered.startswith("import "):
            return True
        if lowered.startswith("/*") or lowered.startswith("*") or lowered.startswith("//"):
            return True
        if "copyright" in lowered or "@author" in lowered:
            return True
        return False

    def _first_meaningful_code_line(self, service: RepositoryContextService, relative_path: str) -> int:
        target = (service.local_path / relative_path).resolve()
        if not target.exists() or not target.is_file():
            return 1
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        in_block_comment = False
        for index, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            if in_block_comment:
                if "*/" in stripped:
                    in_block_comment = False
                continue
            if stripped.startswith("/*"):
                if "*/" not in stripped:
                    in_block_comment = True
                continue
            if stripped.startswith("*") or stripped.startswith("//"):
                continue
            if stripped.startswith("package ") or stripped.startswith("import "):
                continue
            return index
        return 1

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
