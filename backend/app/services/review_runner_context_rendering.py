from __future__ import annotations

from typing import Callable


def normalize_review_observations(
    observations: object,
    *,
    infer_code_language: Callable[[str], str],
) -> list[dict[str, object]]:
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
        related_symbols = [str(value).strip() for value in list(item.get("related_symbols") or []) if str(value).strip()]
        tags = [str(value).strip() for value in list(item.get("tags") or []) if str(value).strip()]
        normalized.append(
            {
                "observation_id": observation_id,
                "kind": kind,
                "signal": str(item.get("signal") or "").strip(),
                "language": str(item.get("language") or "").strip()
                or infer_code_language(str(item.get("file_path") or "")),
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


def resolve_prompt_context_section_order(context_payload: dict[str, object]) -> list[str]:
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


def append_java_ddd_context_summary(
    lines: list[str],
    context_payload: dict[str, object],
    *,
    infer_code_language: Callable[[str], str],
) -> None:
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
        lines.append(f"- Java 通用质量信号: {' / '.join(java_quality_signals[:8])}")
    java_quality_signal_summary = str(context_payload.get("java_quality_signal_summary") or "").strip()
    if java_quality_signal_summary:
        lines.append(f"- Java 通用质量摘要: {java_quality_signal_summary}")
    review_observations = normalize_review_observations(
        context_payload.get("review_observations"),
        infer_code_language=infer_code_language,
    )
    if review_observations:
        lines.append("- 结构化观察点:")
        for item in review_observations[:6]:
            lines.append(
                f"  * {str(item.get('observation_id') or '').strip() or 'observation'} "
                f"@L{int(item.get('line_start') or 1)} "
                f"[{str(item.get('kind') or '').strip() or 'signal'}] "
                f"{str(item.get('summary') or '').strip()}"
            )
    for key in resolve_prompt_context_section_order(context_payload):
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


def build_repository_context_summary(
    repository_context: dict[str, object],
    runtime_tool_results: list[dict[str, object]],
    *,
    infer_code_language: Callable[[str], str],
) -> str:
    lines: list[str] = []
    if repository_context:
        summary = str(repository_context.get("summary") or "").strip()
        if summary:
            lines.append(f"- 主Agent上下文: {summary}")
        change_understanding = repository_context.get("change_understanding")
        if isinstance(change_understanding, dict) and change_understanding:
            risk_domains = [
                str(item).strip()
                for item in list(change_understanding.get("risk_domains") or [])
                if str(item).strip()
            ]
            expert_hints = [
                str(item).strip()
                for item in list(change_understanding.get("expert_hints") or [])
                if str(item).strip()
            ]
            changed_symbols = [
                str(item).strip()
                for item in list(change_understanding.get("changed_symbols") or [])
                if str(item).strip()
            ]
            lines.append("- 结构化变更理解（确定性规则生成，供专家复核，不是最终结论）:")
            lines.append(f"  * 风险域: {' / '.join(risk_domains[:10]) or '无'}")
            lines.append(f"  * 建议专家: {' / '.join(expert_hints[:10]) or '无'}")
            lines.append(f"  * 关键符号: {' / '.join(changed_symbols[:16]) or '无'}")
            files = [item for item in list(change_understanding.get("files") or []) if isinstance(item, dict)]
            for item in files[:6]:
                file_path = str(item.get("path") or "").strip()
                role = str(item.get("file_role") or "").strip()
                domains = [
                    str(value).strip()
                    for value in list(item.get("risk_domains") or [])
                    if str(value).strip()
                ]
                methods = [
                    str(value).strip()
                    for value in list(item.get("changed_methods") or [])
                    if str(value).strip()
                ]
                if file_path:
                    lines.append(
                        f"  * {file_path} · role={role or 'code'} · "
                        f"methods={','.join(methods[:4]) or 'unknown'} · domains={'/'.join(domains[:6]) or 'none'}"
                    )
        primary_context = repository_context.get("primary_context")
        if isinstance(primary_context, dict) and primary_context.get("snippet"):
            lines.append(f"- 目标文件: {primary_context.get('path')}")
            lines.extend(
                f"    {line}"
                for line in str(primary_context.get("snippet") or "").splitlines()[:12]
                if str(line).strip()
            )
        section_order = resolve_prompt_context_section_order(repository_context)
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
        append_java_ddd_context_summary(lines, repository_context, infer_code_language=infer_code_language)
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
        append_java_ddd_context_summary(lines, item, infer_code_language=infer_code_language)
    return "\n".join(lines) if lines else "未补充代码仓上下文。"


def build_observation_review_summary(
    repository_context: dict[str, object],
    *,
    infer_code_language: Callable[[str], str],
) -> str:
    observations = normalize_review_observations(
        repository_context.get("review_observations"),
        infer_code_language=infer_code_language,
    )
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


def build_repository_source_blocks(
    repository_context: dict[str, object],
    runtime_tool_results: list[dict[str, object]],
) -> str:
    lines: list[str] = []
    section_order = resolve_prompt_context_section_order(repository_context)
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
