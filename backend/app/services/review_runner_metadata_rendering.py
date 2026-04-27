from __future__ import annotations


def build_bound_document_metadata(bound_documents: list[object]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for item in bound_documents[:6]:
        title = str(getattr(item, "title", "") or "").strip()
        if not title:
            continue
        outline = [
            str(value).strip()
            for value in list(getattr(item, "indexed_outline", []) or [])[:10]
            if str(value).strip()
        ]
        matched_sections: list[dict[str, object]] = []
        for section in list(getattr(item, "matched_sections", []) or [])[:4]:
            path = str(getattr(section, "path", "") or getattr(section, "title", "") or "").strip()
            summary = str(getattr(section, "summary", "") or "").strip()
            content = str(getattr(section, "content", "") or "").strip()
            snippet = summary or (content.splitlines()[0].strip() if content else "")
            matched_sections.append(
                {
                    "path": path,
                    "summary": snippet,
                    "score": round(float(getattr(section, "score", 0.0) or 0.0), 3),
                    "matched_terms": [
                        str(term).strip()
                        for term in list(getattr(section, "matched_terms", []) or [])[:8]
                        if str(term).strip()
                    ],
                    "matched_signals": [
                        str(signal).strip()
                        for signal in list(getattr(section, "matched_signals", []) or [])[:8]
                        if str(signal).strip()
                    ],
                }
            )
        summaries.append(
            {
                "doc_id": str(getattr(item, "doc_id", "") or "").strip(),
                "title": title,
                "doc_type": str(getattr(item, "doc_type", "") or "").strip(),
                "source_filename": str(getattr(item, "source_filename", "") or "").strip(),
                "indexed_outline": outline,
                "matched_sections": matched_sections,
            }
        )
    return summaries


def build_rule_screening_metadata(rule_screening: dict[str, object]) -> dict[str, object]:
    return {
        "total_rules": int(rule_screening.get("total_rules") or 0),
        "enabled_rules": int(rule_screening.get("enabled_rules") or 0),
        "must_review_count": int(rule_screening.get("must_review_count") or 0),
        "possible_hit_count": int(rule_screening.get("possible_hit_count") or 0),
        "matched_rule_count": int(rule_screening.get("matched_rule_count") or 0),
        "screening_mode": str(rule_screening.get("screening_mode") or "").strip(),
        "screening_fallback_used": bool(rule_screening.get("screening_fallback_used")),
        "total_elapsed_ms": round(float(rule_screening.get("total_elapsed_ms") or 0.0), 2),
        "batch_count": len(list(rule_screening.get("batch_summaries", []) or [])),
        "matched_rules_for_llm": [
            {
                "rule_id": str(item.get("rule_id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "priority": str(item.get("priority") or "").strip(),
                "decision": str(item.get("decision") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "matched_terms": [
                    str(value).strip()
                    for value in list(item.get("matched_terms", []) or [])[:8]
                    if str(value).strip()
                ],
            }
            for item in list(rule_screening.get("matched_rules_for_llm", []) or [])[:6]
            if str(item.get("rule_id") or item.get("title") or "").strip()
        ],
    }


def build_repository_context_metadata(repository_context: dict[str, object]) -> dict[str, object]:
    if not repository_context:
        return {}
    payload: dict[str, object] = {
        "summary": str(repository_context.get("summary") or "").strip(),
        "routing_reason": str(repository_context.get("routing_reason") or "").strip(),
        "java_review_mode": str(repository_context.get("java_review_mode") or "").strip(),
        "java_context_signals": [
            str(item).strip()
            for item in list(repository_context.get("java_context_signals") or [])[:8]
            if str(item).strip()
        ],
        "java_quality_signals": [
            str(item).strip()
            for item in list(repository_context.get("java_quality_signals") or [])[:8]
            if str(item).strip()
        ],
        "java_quality_signal_summary": str(repository_context.get("java_quality_signal_summary") or "").strip(),
        "context_files": [
            str(item).strip()
            for item in list(repository_context.get("context_files") or [])[:8]
            if str(item).strip()
        ],
        "repo_review_instruction_count": len(
            list(
                dict(repository_context.get("repo_review_instructions") or {}).get("instructions")
                or []
            )
        )
        if isinstance(repository_context.get("repo_review_instructions"), dict)
        else 0,
    }

    def _compact_entries(key: str, *, symbol_key: str = "symbol") -> list[dict[str, object]]:
        return [
            {
                "path": str(item.get("path") or "").strip(),
                "symbol": str(item.get(symbol_key) or item.get("class_name") or "").strip(),
                "line_start": int(item.get("line_start") or 0) if item.get("line_start") else 0,
            }
            for item in list(repository_context.get(key) or [])[:4]
            if isinstance(item, dict) and str(item.get("path") or item.get(symbol_key) or item.get("class_name") or "").strip()
        ]

    primary_context = repository_context.get("primary_context")
    if isinstance(primary_context, dict):
        payload["primary_context"] = {
            "path": str(primary_context.get("path") or "").strip(),
            "line_start": int(primary_context.get("line_start") or 0) if primary_context.get("line_start") else 0,
        }

    current_class_context = repository_context.get("current_class_context")
    if isinstance(current_class_context, dict):
        payload["current_class_context"] = {
            "path": str(current_class_context.get("path") or "").strip(),
            "symbol": str(current_class_context.get("symbol") or current_class_context.get("class_name") or "").strip(),
            "line_start": int(current_class_context.get("line_start") or 0) if current_class_context.get("line_start") else 0,
        }

    for key in (
        "related_contexts",
        "related_source_snippets",
        "caller_contexts",
        "callee_contexts",
        "domain_model_contexts",
        "persistence_contexts",
    ):
        entries = _compact_entries(key)
        if entries:
            payload[key] = entries

    transaction_context = repository_context.get("transaction_context")
    if isinstance(transaction_context, dict):
        payload["transaction_context"] = {
            "transactional_method": str(transaction_context.get("transactional_method") or "").strip(),
            "transactional_path": str(transaction_context.get("transactional_path") or "").strip(),
            "call_chain": [
                str(item).strip()
                for item in list(transaction_context.get("call_chain") or [])[:8]
                if str(item).strip()
            ],
        }

    return {key: value for key, value in payload.items() if value not in (None, "", [], {}, 0)}


def build_tool_result_metadata(tool_result: dict[str, object]) -> dict[str, object]:
    payload = {
        "tool_name": str(tool_result.get("tool_name") or "").strip(),
        "summary": str(tool_result.get("summary") or "").strip(),
        "skipped": bool(tool_result.get("skipped")),
        "skip_reason": str(tool_result.get("skip_reason") or "").strip(),
        "signal_summary": str(tool_result.get("signal_summary") or tool_result.get("java_quality_signal_summary") or "").strip(),
        "signals": [
            str(item).strip()
            for item in list(tool_result.get("signals") or tool_result.get("java_quality_signals") or [])[:8]
            if str(item).strip()
        ],
        "context_files": [
            str(item).strip()
            for item in list(tool_result.get("context_files") or [])[:6]
            if str(item).strip()
        ],
        "data_source_summary": dict(tool_result.get("data_source_summary") or {}),
        "matched_tables": [
            str(item).strip()
            for item in list(tool_result.get("matched_tables") or [])[:8]
            if str(item).strip()
        ],
        "table_columns": [dict(item) for item in list(tool_result.get("table_columns") or [])[:12] if isinstance(item, dict)],
        "constraints": [dict(item) for item in list(tool_result.get("constraints") or [])[:12] if isinstance(item, dict)],
        "indexes": [dict(item) for item in list(tool_result.get("indexes") or [])[:12] if isinstance(item, dict)],
        "table_stats": [dict(item) for item in list(tool_result.get("table_stats") or [])[:8] if isinstance(item, dict)],
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def build_runtime_tool_results_metadata(runtime_tool_results: list[dict[str, object]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for item in runtime_tool_results[:8]:
        if not isinstance(item, dict):
            continue
        compact = build_tool_result_metadata(item)
        if compact:
            results.append(compact)
    return results


def build_rule_screening_batch_llm_metadata(batch: dict[str, object]) -> dict[str, object]:
    llm = batch.get("llm")
    if not isinstance(llm, dict):
        return {}
    return {
        "llm_call_id": str(llm.get("llm_call_id") or "").strip(),
        "provider": str(llm.get("provider") or "").strip(),
        "model": str(llm.get("model") or "").strip(),
        "base_url": str(llm.get("base_url") or "").strip(),
        "api_key_env": str(llm.get("api_key_env") or "").strip(),
        "mode": str(llm.get("mode") or "").strip(),
        "llm_error": str(llm.get("llm_error") or "").strip(),
        "elapsed_ms": round(float(llm.get("elapsed_ms") or 0.0), 2),
        "prompt_tokens": int(llm.get("prompt_tokens") or 0),
        "completion_tokens": int(llm.get("completion_tokens") or 0),
        "total_tokens": int(llm.get("total_tokens") or 0),
    }


def build_rule_screening_batch_metadata(batch: dict[str, object]) -> dict[str, object]:
    decisions = []
    for item in list(batch.get("decisions", []) or [])[:24]:
        if not isinstance(item, dict):
            continue
        decisions.append(
            {
                "rule_id": str(item.get("rule_id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "priority": str(item.get("priority") or "").strip(),
                "decision": str(item.get("decision") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "matched_terms": [
                    str(value).strip()
                    for value in list(item.get("matched_terms", []) or [])[:8]
                    if str(value).strip()
                ],
                "matched_signals": [
                    str(value).strip()
                    for value in list(item.get("matched_signals", []) or [])[:8]
                    if str(value).strip()
                ],
            }
        )
    return {
        "batch_index": int(batch.get("batch_index") or 0),
        "batch_count": int(batch.get("batch_count") or 0),
        "screening_mode": str(batch.get("screening_mode") or "").strip(),
        **build_rule_screening_batch_llm_metadata(batch),
        "input_rule_count": int(batch.get("input_rule_count") or 0),
        "must_review_count": int(batch.get("must_review_count") or 0),
        "possible_hit_count": int(batch.get("possible_hit_count") or 0),
        "no_hit_count": int(batch.get("no_hit_count") or 0),
        "input_rules": [
            {
                "rule_id": str(item.get("rule_id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "priority": str(item.get("priority") or "").strip(),
            }
            for item in list(batch.get("input_rules", []) or [])[:24]
            if isinstance(item, dict) and str(item.get("rule_id") or item.get("title") or "").strip()
        ],
        "decisions": decisions,
    }


def build_knowledge_context_metadata(knowledge_context: dict[str, object]) -> dict[str, object]:
    return {
        "focus_file": str(knowledge_context.get("focus_file") or "").strip(),
        "focus_line": int(knowledge_context.get("focus_line") or 0) if knowledge_context.get("focus_line") else 0,
        "changed_files": [
            str(item).strip()
            for item in list(knowledge_context.get("changed_files", []) or [])[:8]
            if str(item).strip()
        ],
        "query_terms": [
            str(item).strip()
            for item in list(knowledge_context.get("query_terms", []) or [])[:12]
            if str(item).strip()
        ],
        "knowledge_sources": [
            str(item).strip()
            for item in list(knowledge_context.get("knowledge_sources", []) or [])[:8]
            if str(item).strip()
        ],
    }
