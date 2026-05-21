from __future__ import annotations

from app.services.cross_file_impact import build_cross_file_impact_hints
from app.services.review_prompt_context_compaction import build_prompt_graph_facts, render_prompt_graph_facts


class ReviewRunnerRenderingMixin:
    """Render repository context, rules and bound documents for prompts."""

    def _build_runtime_tool_summary(self, runtime_tool_results: list[dict[str, object]]) -> str:
        """把运行时工具结果压缩成适合再次输入 LLM 的摘要。"""
        if not runtime_tool_results:
            return "无可用运行时工具或本轮未命中可调用工具。"
        lines: list[str] = []
        for item in runtime_tool_results:
            tool_name = str(item.get("tool_name") or "")
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"- {tool_name}: {summary}")
            else:
                lines.append(f"- {tool_name}: 已执行")
            matches = item.get("matches")
            if isinstance(matches, list) and matches:
                for match in matches[:2]:
                    if isinstance(match, dict):
                        title = str(match.get("title") or match.get("doc_id") or "knowledge")
                        snippet = str(match.get("snippet") or "").strip()
                        lines.append(f"  * {title}: {snippet[:160]}")
            if tool_name == "pg_schema_context":
                data_source_summary = item.get("data_source_summary")
                if isinstance(data_source_summary, dict):
                    database = str(data_source_summary.get("database") or "").strip()
                    host = str(data_source_summary.get("host") or "").strip()
                    schema_allowlist = [
                        str(value).strip()
                        for value in list(data_source_summary.get("schema_allowlist") or [])
                        if str(value).strip()
                    ]
                    source_line = "  * 数据源: "
                    source_line += database or "unknown_db"
                    if host:
                        source_line += f" @ {host}"
                    if schema_allowlist:
                        source_line += f" · schema={', '.join(schema_allowlist[:4])}"
                    lines.append(source_line)
                matched_tables = [
                    str(value).strip()
                    for value in list(item.get("matched_tables") or [])
                    if str(value).strip()
                ]
                if matched_tables:
                    lines.append(f"  * 命中表: {' / '.join(matched_tables[:6])}")
                table_columns = item.get("table_columns")
                if isinstance(table_columns, list) and table_columns:
                    formatted_columns: list[str] = []
                    for column in table_columns[:8]:
                        if not isinstance(column, dict):
                            continue
                        table_name = str(column.get("table_name") or "").strip()
                        column_name = str(column.get("column_name") or "").strip()
                        data_type = str(column.get("data_type") or "").strip()
                        nullable = str(column.get("is_nullable") or "").strip()
                        if table_name and column_name:
                            formatted_columns.append(
                                f"{table_name}.{column_name}({data_type or 'unknown'} / nullable={nullable or 'unknown'})"
                            )
                    if formatted_columns:
                        lines.append(f"  * 关键列: {' / '.join(formatted_columns[:6])}")
                constraints = item.get("constraints")
                if isinstance(constraints, list) and constraints:
                    formatted_constraints: list[str] = []
                    for constraint in constraints[:6]:
                        if not isinstance(constraint, dict):
                            continue
                        table_name = str(constraint.get("table_name") or "").strip()
                        constraint_type = str(constraint.get("constraint_type") or "").strip()
                        columns = str(constraint.get("columns") or "").strip()
                        if table_name and constraint_type:
                            formatted_constraints.append(f"{table_name}:{constraint_type}({columns})" if columns else f"{table_name}:{constraint_type}")
                    if formatted_constraints:
                        lines.append(f"  * 约束: {' / '.join(formatted_constraints[:5])}")
                indexes = item.get("indexes")
                if isinstance(indexes, list) and indexes:
                    formatted_indexes: list[str] = []
                    for index in indexes[:6]:
                        if not isinstance(index, dict):
                            continue
                        table_name = str(index.get("table_name") or "").strip()
                        indexname = str(index.get("indexname") or "").strip()
                        if table_name and indexname:
                            formatted_indexes.append(f"{table_name}:{indexname}")
                    if formatted_indexes:
                        lines.append(f"  * 索引: {' / '.join(formatted_indexes[:5])}")
                table_stats = item.get("table_stats")
                if isinstance(table_stats, list) and table_stats:
                    formatted_stats: list[str] = []
                    for stat in table_stats[:5]:
                        if not isinstance(stat, dict):
                            continue
                        table_name = str(stat.get("table_name") or "").strip()
                        estimated_rows = stat.get("estimated_rows")
                        total_size = str(stat.get("total_size") or "").strip()
                        if table_name:
                            row_part = f"rows≈{estimated_rows}" if estimated_rows not in (None, "") else "rows≈unknown"
                            size_part = f" size={total_size}" if total_size else ""
                            formatted_stats.append(f"{table_name}:{row_part}{size_part}")
                    if formatted_stats:
                        lines.append(f"  * 表统计: {' / '.join(formatted_stats[:4])}")
        return "\n".join(lines)

    def _build_repository_context_summary(
        self,
        repository_context: dict[str, object],
        runtime_tool_results: list[dict[str, object]],
    ) -> str:
        """整合主 Agent 和 repo_context_search 提供的代码仓上下文摘要。"""
        lines: list[str] = []
        if repository_context:
            summary = str(repository_context.get("summary") or "").strip()
            if summary:
                lines.append(f"- 主Agent上下文: {summary}")
            graph_fact_lines = render_prompt_graph_facts(build_prompt_graph_facts(repository_context))
            if graph_fact_lines:
                lines.extend(graph_fact_lines)
            code_graph_minimal = repository_context.get("code_graph_minimal_context")
            if isinstance(code_graph_minimal, dict) and code_graph_minimal:
                graph_summary = str(code_graph_minimal.get("summary") or "").strip()
                risk_level = str(code_graph_minimal.get("risk_level") or "").strip()
                risk_score = code_graph_minimal.get("risk_score")
                if graph_summary:
                    risk_part = f"；风险 {risk_level}/{risk_score}" if risk_level else ""
                    lines.append(f"- Tree-sitter 图谱摘要: {graph_summary}{risk_part}")
                key_entities = [
                    str(item).strip()
                    for item in list(code_graph_minimal.get("key_entities") or [])
                    if str(item).strip()
                ]
                if key_entities:
                    lines.append(f"  * 变更节点: {' / '.join(key_entities[:5])}")
            code_graph_impact = repository_context.get("code_graph_impact_analysis")
            if isinstance(code_graph_impact, dict) and code_graph_impact:
                impacted_files = [
                    str(item).strip()
                    for item in list(code_graph_impact.get("impacted_files") or [])
                    if str(item).strip()
                ]
                if impacted_files:
                    lines.append(f"  * 候选受影响文件: {' / '.join(impacted_files[:6])}")
                test_gaps = [
                    item
                    for item in list(code_graph_impact.get("test_gaps") or [])
                    if isinstance(item, dict)
                ]
                if test_gaps:
                    formatted_gaps = [
                        str(item.get("qualified_name") or item.get("name") or "").strip()
                        for item in test_gaps[:5]
                        if str(item.get("qualified_name") or item.get("name") or "").strip()
                    ]
                    if formatted_gaps:
                        lines.append(f"  * 测试覆盖缺口: {' / '.join(formatted_gaps)}")
            sast_prescan = repository_context.get("sast_prescan")
            if isinstance(sast_prescan, dict):
                sast_summary = str(sast_prescan.get("summary") or "").strip()
                if sast_summary:
                    lines.append(f"- SAST/linter 预扫描: {sast_summary}")
                for item in list(sast_prescan.get("findings") or [])[:5]:
                    if not isinstance(item, dict):
                        continue
                    tool = str(item.get("tool") or "").strip()
                    rule_id = str(item.get("rule_id") or "").strip()
                    message = str(item.get("message") or "").strip()
                    cwe = str(item.get("cwe") or "").strip()
                    why = str(item.get("why_it_matters") or "").strip()
                    line_start = int(item.get("line_start") or 1)
                    if message:
                        cwe_text = f" ({cwe})" if cwe else ""
                        why_text = f"；{why}" if why else ""
                        lines.append(f"  * {tool or 'sast'}:{rule_id or 'rule'} L{line_start}{cwe_text} {message}{why_text}")
            primary_context = repository_context.get("primary_context")
            if isinstance(primary_context, dict) and primary_context.get("snippet"):
                lines.append(f"- 目标文件: {primary_context.get('path')}")
                lines.extend(
                    f"    {line}"
                    for line in str(primary_context.get("snippet") or "").splitlines()[:12]
                    if str(line).strip()
                )
            section_order = self._resolve_prompt_context_section_order(repository_context)
            for section_key in section_order:
                if section_key == "current_class_context":
                    current_class_context = repository_context.get("current_class_context")
                    if isinstance(current_class_context, dict) and current_class_context.get("snippet"):
                        class_name = str(current_class_context.get("class_name") or "").strip()
                        method_name = str(current_class_context.get("method_name") or "").strip()
                        path = str(current_class_context.get("path") or "").strip()
                        title = path or "当前类"
                        if class_name or method_name:
                            title += f" · {class_name or 'UnknownClass'}::{method_name or 'unknownMethod'}"
                        lines.append(f"- 当前类实现片段: {title}")
                        lines.extend(
                            f"    {line}"
                            for line in str(current_class_context.get("snippet") or "").splitlines()[:12]
                            if str(line).strip()
                        )
                if section_key == "transaction_context":
                    transaction_context = repository_context.get("transaction_context")
                    if isinstance(transaction_context, dict) and transaction_context:
                        transaction_path = str(transaction_context.get("transactional_path") or "").strip()
                        method_name = str(transaction_context.get("transactional_method") or "").strip()
                        boundary_snippet = str(transaction_context.get("transaction_boundary_snippet") or "").strip()
                        header = transaction_path or "事务边界"
                        if method_name:
                            header += f" · {method_name}"
                        if boundary_snippet:
                            lines.append(f"- 事务边界: {header}")
                            lines.extend(f"    {line}" for line in boundary_snippet.splitlines()[:10] if str(line).strip())
                        call_chain = [
                            str(item).strip()
                            for item in list(transaction_context.get("call_chain") or [])
                            if str(item).strip()
                        ]
                        if call_chain:
                            lines.append(f"- 事务调用链: {' -> '.join(call_chain[:6])}")
                if section_key == "related_contexts":
                    related_contexts = repository_context.get("related_contexts")
                    if isinstance(related_contexts, list) and related_contexts:
                        lines.append("- 关联文件源码片段:")
                        for item in related_contexts[:4]:
                            if not isinstance(item, dict):
                                continue
                            related_path = str(item.get("path") or "").strip()
                            related_snippet = str(item.get("snippet") or "").strip()
                            if not related_path or not related_snippet:
                                continue
                            lines.append(f"  * {related_path}")
                            lines.extend(f"    {line}" for line in related_snippet.splitlines()[:12])
                if section_key in {
                    "parent_contract_contexts",
                    "caller_contexts",
                    "callee_contexts",
                    "domain_model_contexts",
                    "persistence_contexts",
                }:
                    section_labels = {
                        "parent_contract_contexts": "父接口/抽象类",
                        "caller_contexts": "调用方",
                        "callee_contexts": "被调方",
                        "domain_model_contexts": "领域模型",
                        "persistence_contexts": "持久化上下文",
                    }
                    contexts = repository_context.get(section_key)
                    if isinstance(contexts, list) and contexts:
                        lines.append(f"- {section_labels[section_key]}:")
                        for item in contexts[:3]:
                            if not isinstance(item, dict):
                                continue
                            path = str(item.get("path") or "").strip()
                            snippet = str(item.get("snippet") or "").strip()
                            symbol = str(item.get("symbol") or "").strip()
                            if not path or not snippet:
                                continue
                            header = path
                            if symbol:
                                header += f" · {symbol}"
                            lines.append(f"  * {header}")
                            lines.extend(f"    {line}" for line in snippet.splitlines()[:10])
                if section_key == "related_source_snippets":
                    related_source_snippets = repository_context.get("related_source_snippets")
                    if isinstance(related_source_snippets, list) and related_source_snippets:
                        lines.append("- 关联源码片段:")
                        for snippet_item in related_source_snippets[:3]:
                            if not isinstance(snippet_item, dict):
                                continue
                            path = str(snippet_item.get("path") or "").strip()
                            kind = str(snippet_item.get("kind") or "").strip()
                            symbol = str(snippet_item.get("symbol") or "").strip()
                            snippet = str(snippet_item.get("snippet") or "").strip()
                            if not path or not snippet:
                                continue
                            header = path
                            if kind or symbol:
                                header += f"（{kind or 'context'} / {symbol or 'n/a'}）"
                            lines.append(f"  * {header}")
                            lines.extend(f"    {line}" for line in snippet.splitlines()[:8])
                if section_key == "symbol_contexts":
                    symbol_contexts = repository_context.get("symbol_contexts")
                    if isinstance(symbol_contexts, list) and symbol_contexts:
                        for item in symbol_contexts[:2]:
                            if not isinstance(item, dict):
                                continue
                            symbol = str(item.get("symbol") or "").strip()
                            definition_count = len(list(item.get("definitions") or []))
                            reference_count = len(list(item.get("references") or []))
                            if symbol:
                                lines.append(f"- 符号上下文: {symbol} · 定义 {definition_count} · 引用 {reference_count}")
                            definitions = item.get("definitions")
                            if isinstance(definitions, list) and definitions:
                                for definition in definitions[:2]:
                                    if not isinstance(definition, dict):
                                        continue
                                    path = str(definition.get("path") or "").strip()
                                    snippet = str(definition.get("snippet") or "").strip()
                                    if not path or not snippet:
                                        continue
                                    lines.append(f"  * 定义: {path}")
                                    lines.extend(f"    {line}" for line in snippet.splitlines()[:8])
                            references = item.get("references")
                            if isinstance(references, list) and references:
                                for reference in references[:2]:
                                    if not isinstance(reference, dict):
                                        continue
                                    path = str(reference.get("path") or "").strip()
                                    snippet = str(reference.get("snippet") or "").strip()
                                    if not path or not snippet:
                                        continue
                                    lines.append(f"  * 引用: {path}")
                                    lines.extend(f"    {line}" for line in snippet.splitlines()[:8])
            self._append_java_ddd_context_summary(lines, repository_context)
        for item in runtime_tool_results:
            if str(item.get("tool_name") or "") != "repo_context_search":
                continue
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"- Repo 工具: {summary}")
            context_files = [
                str(value).strip()
                for value in list(item.get("context_files") or [])
                if str(value).strip()
            ]
            if context_files:
                lines.append(f"- Repo 引用文件: {' / '.join(context_files[:4])}")
            matches = item.get("matches")
            if isinstance(matches, list) and matches:
                formatted = []
                for match in matches[:3]:
                    if isinstance(match, dict):
                        path = str(match.get("path") or "").strip()
                        line_number = match.get("line_number")
                        if path:
                            formatted.append(f"{path}:{line_number}" if line_number else path)
                if formatted:
                    lines.append(f"- 代码仓命中: {' / '.join(formatted)}")
            keyword_sources = item.get("search_keyword_sources")
            if isinstance(keyword_sources, list) and keyword_sources:
                formatted_keywords: list[str] = []
                for keyword_source in keyword_sources[:3]:
                    if not isinstance(keyword_source, dict):
                        continue
                    keyword = str(keyword_source.get("keyword") or "").strip()
                    source_label = str(keyword_source.get("source_label") or keyword_source.get("source") or "").strip()
                    if keyword and source_label:
                        formatted_keywords.append(f"{keyword}({source_label})")
                    elif keyword:
                        formatted_keywords.append(keyword)
                if formatted_keywords:
                    lines.append(f"- Repo 关键词来源: {' / '.join(formatted_keywords)}")
            symbol_contexts = item.get("symbol_contexts")
            if isinstance(symbol_contexts, list) and symbol_contexts:
                for symbol_context in symbol_contexts[:2]:
                    if not isinstance(symbol_context, dict):
                        continue
                    symbol = str(symbol_context.get("symbol") or "").strip()
                    if not symbol:
                        continue
                    lines.append(
                        f"- Repo 符号: {symbol} · 定义 {len(list(symbol_context.get('definitions') or []))} · "
                        f"引用 {len(list(symbol_context.get('references') or []))}"
                    )
            related_source_snippets = item.get("related_source_snippets")
            if isinstance(related_source_snippets, list) and related_source_snippets:
                lines.append("- 关联源码片段:")
                for snippet_item in related_source_snippets[:3]:
                    if not isinstance(snippet_item, dict):
                        continue
                    path = str(snippet_item.get("path") or "").strip()
                    kind = str(snippet_item.get("kind") or "").strip()
                    symbol = str(snippet_item.get("symbol") or "").strip()
                    snippet = str(snippet_item.get("snippet") or "").strip()
                    if not path or not snippet:
                        continue
                    header = path
                    if kind or symbol:
                        header += f"（{kind or 'context'} / {symbol or 'n/a'}）"
                    lines.append(f"  * {header}")
                    lines.extend(f"    {line}" for line in snippet.splitlines()[:8])
            self._append_java_ddd_context_summary(lines, item)
        return "\n".join(lines) if lines else "未补充代码仓上下文。"

    def _normalize_review_observations(self, observations: object) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for item in list(observations or []):
            if not isinstance(item, dict):
                continue
            observation_id = str(item.get("observation_id") or "").strip()
            kind = str(item.get("kind") or "").strip()
            summary = str(item.get("summary") or "").strip()
            try:
                line_start = int(item.get("line_start") or 1)
            except Exception:
                line_start = 1
            try:
                line_end = int(item.get("line_end") or line_start or 1)
            except Exception:
                line_end = line_start
            risk_hints = [str(value).strip() for value in list(item.get("risk_hints") or []) if str(value).strip()]
            evidence = [str(value).strip() for value in list(item.get("evidence") or []) if str(value).strip()]
            related_symbols = [
                str(value).strip() for value in list(item.get("related_symbols") or []) if str(value).strip()
            ]
            tags = [str(value).strip() for value in list(item.get("tags") or []) if str(value).strip()]
            normalized.append(
                {
                    "observation_id": observation_id,
                    "kind": kind,
                    "signal": str(item.get("signal") or "").strip(),
                    "language": str(item.get("language") or "").strip() or self._infer_code_language(str(item.get("file_path") or "")),
                    "file_path": str(item.get("file_path") or "").strip(),
                    "line_start": line_start,
                    "line_end": line_end,
                    "summary": summary,
                    "risk_hints": risk_hints[:4],
                    "evidence": evidence[:3],
                    "related_symbols": related_symbols[:4],
                    "tags": tags[:4],
                    "confidence": float(item.get("confidence") or 0.0),
                }
            )
        return normalized

    def _build_observation_review_summary(self, repository_context: dict[str, object]) -> str:
        observations = self._normalize_review_observations(repository_context.get("review_observations"))
        if not observations:
            return "- 未提炼到额外结构化观察点；请仍按规范、diff 和源码上下文独立审查。"
        lines = [
            "- 以下 observation 只是待复核的代码现象，不是预设结论；请逐条判断其是否构成真实问题。"
        ]
        for item in observations[:8]:
            observation_id = str(item.get("observation_id") or "").strip()
            line_start = int(item.get("line_start") or 1)
            kind = str(item.get("kind") or "").strip()
            summary = str(item.get("summary") or "").strip()
            lines.append(f"- {observation_id or 'observation'} @L{line_start} [{kind or 'signal'}] {summary}")
            risk_hints = [str(value).strip() for value in list(item.get("risk_hints") or []) if str(value).strip()]
            if risk_hints:
                lines.append(f"  风险提示: {' / '.join(risk_hints[:3])}")
            evidence = [str(value).strip() for value in list(item.get("evidence") or []) if str(value).strip()]
            if evidence:
                lines.append(f"  证据: {' / '.join(evidence[:2])}")
        return "\n".join(lines)

    def _build_repo_review_instruction_summary(self, repository_context: dict[str, object]) -> str:
        raw = repository_context.get("repo_review_instructions")
        if not isinstance(raw, dict):
            return ""
        summary = str(raw.get("summary") or "").strip()
        if summary:
            return summary
        instructions = [
            item
            for item in list(raw.get("instructions") or [])
            if isinstance(item, dict) and str(item.get("content") or "").strip()
        ]
        if not instructions:
            return ""
        lines = ["仓库内检视规则："]
        for item in instructions[:6]:
            title = str(item.get("title") or item.get("source") or "规则").strip()
            source_path = str(item.get("path") or "").strip()
            content = " ".join(
                line.strip()
                for line in str(item.get("content") or "").splitlines()
                if line.strip()
            )
            label = f"- {title}"
            if source_path:
                label += f"（{source_path}）"
            lines.append(label)
            if content:
                lines.append(f"  {content[:420]}")
        return "\n".join(lines)

    def _build_repository_source_blocks(
        self,
        repository_context: dict[str, object],
        runtime_tool_results: list[dict[str, object]],
    ) -> str:
        lines: list[str] = []
        section_order = self._resolve_prompt_context_section_order(repository_context)
        primary_context = repository_context.get("primary_context")
        if isinstance(primary_context, dict):
            path = str(primary_context.get("path") or "").strip()
            snippet = str(primary_context.get("snippet") or "").strip()
            if path and snippet:
                lines.append(f"# 目标文件源码\n{path}")
                lines.extend(snippet.splitlines()[:20])

        section_labels = {
            "current_class_context": "当前类问题片段",
            "parent_contract_contexts": "父接口/抽象类",
            "caller_contexts": "调用方",
            "callee_contexts": "被调方",
            "domain_model_contexts": "领域模型",
            "persistence_contexts": "持久化上下文",
            "related_contexts": "关联上下文",
            "related_source_snippets": "关联源码片段",
            "transaction_context": "事务边界",
        }
        for key in section_order:
            if key == "current_class_context":
                current_class_context = repository_context.get("current_class_context")
                if isinstance(current_class_context, dict):
                    path = str(current_class_context.get("path") or "").strip()
                    snippet = str(current_class_context.get("snippet") or "").strip()
                    if path and snippet:
                        if lines:
                            lines.append("")
                        lines.append(f"# {section_labels[key]}\n{path}")
                        lines.extend(snippet.splitlines()[:20])
                continue
            if key == "transaction_context":
                transaction_context = repository_context.get("transaction_context")
                if isinstance(transaction_context, dict) and transaction_context:
                    snippet = str(transaction_context.get("transaction_boundary_snippet") or "").strip()
                    path = str(transaction_context.get("transactional_path") or "").strip()
                    method_name = str(transaction_context.get("transactional_method") or "").strip()
                    call_chain = [
                        str(item).strip()
                        for item in list(transaction_context.get("call_chain") or [])
                        if str(item).strip()
                    ]
                    if snippet:
                        if lines:
                            lines.append("")
                        header = f"# {section_labels[key]}\n{path}"
                        if method_name:
                            header += f" · {method_name}"
                        lines.append(header)
                        lines.extend(snippet.splitlines()[:16])
                    if call_chain:
                        lines.append(f"调用链: {' -> '.join(call_chain[:6])}")
                continue
            if key == "symbol_contexts":
                continue
            if key not in section_labels:
                continue
            label = section_labels[key]
            contexts = repository_context.get(key)
            if not isinstance(contexts, list):
                continue
            appended = 0
            for item in contexts[:2]:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                snippet = str(item.get("snippet") or "").strip()
                symbol = str(item.get("symbol") or "").strip()
                if not path or not snippet:
                    continue
                if lines:
                    lines.append("")
                header = f"# {label}\n{path}"
                if symbol:
                    header += f" · {symbol}"
                lines.append(header)
                lines.extend(snippet.splitlines()[:16])
                appended += 1
            if appended:
                continue

        if not lines:
            for item in runtime_tool_results:
                if str(item.get("tool_name") or "") != "repo_context_search":
                    continue
                primary = item.get("primary_context")
                if isinstance(primary, dict):
                    path = str(primary.get("path") or "").strip()
                    snippet = str(primary.get("snippet") or "").strip()
                    if path and snippet:
                        lines.append(f"# 目标文件源码\n{path}")
                        lines.extend(snippet.splitlines()[:20])
                        break
        return "\n".join(lines) if lines else "未补充可直接供大模型阅读的源码上下文。"

    def _resolve_prompt_context_section_order(self, context_payload: dict[str, object]) -> list[str]:
        section_order = [
            str(item).strip()
            for item in list(context_payload.get("prompt_context_section_order") or [])
            if str(item).strip()
        ]
        if section_order:
            return section_order
        return [
            "current_class_context",
            "caller_contexts",
            "callee_contexts",
            "domain_model_contexts",
            "transaction_context",
            "persistence_contexts",
            "parent_contract_contexts",
            "related_contexts",
            "related_source_snippets",
            "symbol_contexts",
        ]

    def _append_java_ddd_context_summary(self, lines: list[str], context_payload: dict[str, object]) -> None:
        java_review_mode = str(context_payload.get("java_review_mode") or "").strip()
        java_context_signals = [
            str(item).strip()
            for item in list(context_payload.get("java_context_signals") or [])
            if str(item).strip()
        ]
        if java_review_mode:
            mode_label = "Java DDD 增强模式" if java_review_mode == "ddd_enhanced" else "Java 通用模式"
            lines.append(f"- Java 审查模式: {mode_label}")
        if java_context_signals:
            lines.append(f"- Java 结构信号: {' / '.join(java_context_signals[:8])}")
        java_quality_signals = [
            str(item).strip()
            for item in list(context_payload.get("java_quality_signals") or [])
            if str(item).strip()
        ]
        if java_quality_signals:
            lines.append(f"- 语言通用质量信号: {' / '.join(java_quality_signals[:8])}")
        java_quality_signal_summary = str(context_payload.get("java_quality_signal_summary") or "").strip()
        if java_quality_signal_summary:
            lines.append(f"- 语言通用质量摘要: {java_quality_signal_summary}")
        cross_file_impact_hints = build_cross_file_impact_hints(
            file_path=str((context_payload.get("primary_context") or {}).get("path") or ""),
            repository_context=context_payload,
        )
        if cross_file_impact_hints:
            lines.append("- 跨文件影响提示:")
            for item in cross_file_impact_hints[:4]:
                lines.append(f"  * {item}")
        review_observations = self._normalize_review_observations(context_payload.get("review_observations"))
        if review_observations:
            lines.append("- 结构化观察点:")
            for item in review_observations[:6]:
                lines.append(
                    f"  * {str(item.get('observation_id') or '').strip() or 'observation'} "
                    f"@L{int(item.get('line_start') or 1)} "
                    f"[{str(item.get('kind') or '').strip() or 'signal'}] "
                    f"{str(item.get('summary') or '').strip()}"
                )
        for key in self._resolve_prompt_context_section_order(context_payload):
            if key == "current_class_context":
                current_class_context = context_payload.get("current_class_context")
                if isinstance(current_class_context, dict) and current_class_context.get("snippet"):
                    lines.append("- Java 当前类问题片段:")
                    lines.append(
                        f"  * {str(current_class_context.get('path') or '').strip()} "
                        f"{str(current_class_context.get('class_name') or '').strip()}::"
                        f"{str(current_class_context.get('method_name') or '').strip()}"
                    )
                    lines.extend(
                        f"    {line}"
                        for line in str(current_class_context.get("snippet") or "").splitlines()[:14]
                        if str(line).strip()
                    )
                continue
            labels = {
                "parent_contract_contexts": "父接口/抽象类",
                "caller_contexts": "调用方",
                "callee_contexts": "被调方",
                "domain_model_contexts": "领域模型",
                "persistence_contexts": "持久化上下文",
            }
            if key in labels:
                label = labels[key]
                contexts = context_payload.get(key)
                if not isinstance(contexts, list) or not contexts:
                    continue
                lines.append(f"- Java {label}:")
                for item in contexts[:3]:
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path") or "").strip()
                    snippet = str(item.get("snippet") or "").strip()
                    symbol = str(item.get("symbol") or "").strip()
                    if not path or not snippet:
                        continue
                    header = path
                    if symbol:
                        header += f" · {symbol}"
                    lines.append(f"  * {header}")
                    lines.extend(f"    {line}" for line in snippet.splitlines()[:10])
                continue
            if key != "transaction_context":
                continue
            transaction_context = context_payload.get("transaction_context")
            if not isinstance(transaction_context, dict) or not transaction_context:
                continue
            lines.append("- Java 事务边界:")
            method_name = str(transaction_context.get("transactional_method") or "").strip()
            transaction_path = str(transaction_context.get("transactional_path") or "").strip()
            if transaction_path or method_name:
                lines.append(f"  * {transaction_path} · {method_name}")
            boundary_snippet = str(transaction_context.get("transaction_boundary_snippet") or "").strip()
            if boundary_snippet:
                lines.extend(f"    {line}" for line in boundary_snippet.splitlines()[:10])
            call_chain = [str(item).strip() for item in list(transaction_context.get("call_chain") or []) if str(item).strip()]
            if call_chain:
                lines.append(f"  * 调用链: {' -> '.join(call_chain[:6])}")

    def _build_review_spec_summary(self, review_spec: str) -> str:
        if not review_spec.strip():
            return "未提供额外规范文档，请至少遵守职责边界、证据优先、修复建议可执行三条规则。"
        lines = [line.strip() for line in review_spec.splitlines() if line.strip()]
        return "\n".join(lines[:18])

    def _build_bound_documents_summary(self, bound_documents: list[object]) -> str:
        if not bound_documents:
            return "未绑定额外专家参考文档。"
        lines: list[str] = []
        for item in bound_documents[:8]:
            title = str(getattr(item, "title", "") or "").strip() or "未命名文档"
            doc_type = str(getattr(item, "doc_type", "") or "reference").strip()
            source_filename = str(getattr(item, "source_filename", "") or "").strip()
            tags = [str(tag).strip() for tag in list(getattr(item, "tags", []) or []) if str(tag).strip()]
            line = f"- [{doc_type}] {title}"
            if source_filename:
                line += f" · {source_filename}"
            if tags:
                line += f" · 标签: {' / '.join(tags[:4])}"
            matched_sections = list(getattr(item, "matched_sections", []) or [])
            outline = [str(value).strip() for value in list(getattr(item, "indexed_outline", []) or []) if str(value).strip()]
            if matched_sections:
                matched_paths = [
                    str(getattr(section, "path", "") or "").strip()
                    for section in matched_sections[:2]
                    if str(getattr(section, "path", "") or "").strip()
                ]
                if matched_paths:
                    line += f" · 命中章节: {' / '.join(matched_paths)}"
            elif outline:
                line += f" · 章节索引: {' / '.join(outline[:3])}"
            lines.append(line)
        return "\n".join(lines)

    def _build_bound_documents_fulltext(self, bound_documents: list[object]) -> str:
        if not bound_documents:
            return "《专家绑定参考文档》开始\n未绑定额外专家参考文档。\n《专家绑定参考文档》结束"
        sections: list[str] = ["《专家绑定参考文档》开始"]
        for index, item in enumerate(bound_documents, start=1):
            title = str(getattr(item, "title", "") or "").strip() or f"文档 {index}"
            doc_type = str(getattr(item, "doc_type", "") or "reference").strip()
            source_filename = str(getattr(item, "source_filename", "") or "").strip()
            matched_sections = list(getattr(item, "matched_sections", []) or [])
            outline = [str(value).strip() for value in list(getattr(item, "indexed_outline", []) or []) if str(value).strip()]
            sections.append(f"## 文档 {index}: {title}")
            sections.append(f"- 类型: {doc_type}")
            if source_filename:
                sections.append(f"- 来源文件: {source_filename}")
            if matched_sections:
                sections.append("- 命中章节如下：")
                for section in matched_sections[:6]:
                    path = str(getattr(section, "path", "") or "").strip() or str(getattr(section, "title", "") or "").strip()
                    summary = str(getattr(section, "summary", "") or "").strip()
                    content = str(getattr(section, "content", "") or "").strip() or "空章节"
                    sections.append(f"### {path}")
                    if summary:
                        sections.append(f"摘要: {summary}")
                    sections.append(content[:1600])
            elif outline:
                sections.append("- 未命中具体章节，以下为文档目录索引：")
                sections.extend([f"  - {value}" for value in outline[:12]])
            else:
                content = str(getattr(item, "content", "") or "").strip() or "空文档"
                sections.append(content[:2000])
        sections.append("《专家绑定参考文档》结束")
        return "\n".join(sections)

    def _build_rule_screening_summary(self, rule_screening: dict[str, object]) -> str:
        total_rules = int(rule_screening.get("total_rules") or 0)
        if total_rules <= 0:
            return "当前未绑定可执行规则卡。"
        must_review_count = int(rule_screening.get("must_review_count") or 0)
        possible_hit_count = int(rule_screening.get("possible_hit_count") or 0)
        lines = [
            f"- 已遍历规则: {total_rules}",
            f"- 强命中规则: {must_review_count}",
            f"- 候选规则: {possible_hit_count}",
        ]
        matched_rules = list(rule_screening.get("matched_rules_for_llm", []) or [])
        if matched_rules:
            lines.append("- 本轮优先带入审查的规则:")
            for item in matched_rules[:5]:
                rule_id = str(item.get("rule_id") or item.get("id") or "").strip()
                title = str(item.get("title") or item.get("rule_id") or "").strip()
                priority = str(item.get("priority") or "P2").strip()
                scene_path = str(item.get("scene_path") or "").strip()
                reason = str(item.get("reason") or "").strip()
                if title:
                    label = f"[{priority}] {rule_id} {title}".strip() if rule_id else f"[{priority}] {title}"
                    if scene_path:
                        label = f"{label}（{scene_path}）"
                    lines.append(f"  - {label} · {reason or '命中规则信号'}")
        return "\n".join(lines)

    def _build_rule_screening_fulltext(self, rule_screening: dict[str, object]) -> str:
        total_rules = int(rule_screening.get("total_rules") or 0)
        if total_rules <= 0:
            return "《规则遍历结果》开始\n当前未绑定可执行规则卡。\n《规则遍历结果》结束"
        sections = [
            "《规则遍历结果》开始",
            f"- 已遍历规则总数: {total_rules}",
            f"- 强命中规则数: {int(rule_screening.get('must_review_count') or 0)}",
            f"- 候选规则数: {int(rule_screening.get('possible_hit_count') or 0)}",
        ]
        matched_rules = list(rule_screening.get("matched_rules_for_llm", []) or [])
        if matched_rules:
            sections.append("- 本轮应优先遵守并逐条核查的规则卡：")
            for item in matched_rules[:6]:
                title = str(item.get("title") or item.get("rule_id") or "").strip()
                priority = str(item.get("priority") or "P2").strip()
                scene_path = str(item.get("scene_path") or "").strip()
                description = str(item.get("description") or "").strip()
                language = str(item.get("language") or "").strip()
                matched_terms = [
                    str(value).strip()
                    for value in list(item.get("matched_terms", []) or [])[:6]
                    if str(value).strip()
                ]
                sections.append(f"## [{priority}] {title}")
                if scene_path:
                    sections.append(f"场景路径: {scene_path}")
                if description:
                    sections.append(f"规则描述: {description}")
                if language:
                    sections.append(f"语言: {language}")
                if matched_terms:
                    sections.append(f"命中关键词: {' / '.join(matched_terms)}")
                problem_code_example = str(item.get("problem_code_example") or "").strip()
                problem_code_line = str(item.get("problem_code_line") or "").strip()
                false_positive_code = str(item.get("false_positive_code") or "").strip()
                if problem_code_example:
                    sections.append("问题代码示例:")
                    sections.append(problem_code_example[:800])
                if problem_code_line:
                    sections.append(f"重点关注代码行模式: {problem_code_line[:500]}")
                if false_positive_code:
                    sections.append("误报代码参考:")
                    sections.append(false_positive_code[:800])
        sections.append("《规则遍历结果》结束")
        return "\n".join(sections)
