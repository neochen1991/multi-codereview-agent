from __future__ import annotations

import re
from typing import Any


def build_cross_file_impact_hints(
    *,
    file_path: str,
    related_files: list[str] | None = None,
    repo_hits: dict[str, Any] | None = None,
    repository_context: dict[str, Any] | None = None,
    changed_files: list[str] | None = None,
    target_hunk_excerpt: str = "",
) -> list[str]:
    hints: list[str] = []
    focus_path = str(file_path or "").strip()
    normalized_related_files: list[str] = []
    related_paths_seen: set[str] = set()

    for item in list(related_files or []):
        path = str(item or "").strip()
        if not path or path == focus_path or path in related_paths_seen:
            continue
        related_paths_seen.add(path)
        normalized_related_files.append(path)

    repo_payload = dict(repo_hits or {})
    context_payload = dict(repository_context or {})
    normalized_changed_files = {
        str(item).strip()
        for item in [*(changed_files or []), *list(context_payload.get("changed_files") or [])]
        if str(item).strip()
    }

    for item in list(context_payload.get("context_files") or []):
        path = str(item or "").strip()
        if not path or path == focus_path or path in related_paths_seen:
            continue
        related_paths_seen.add(path)
        normalized_related_files.append(path)

    if normalized_related_files:
        preview = " / ".join(normalized_related_files[:3])
        suffix = " 等" if len(normalized_related_files) > 3 else ""
        hints.append(f"当前改动会联动 {len(normalized_related_files)} 个关联文件：{preview}{suffix}")

    call_chain_paths = _collect_context_paths(
        context_payload,
        keys=("caller_contexts", "callee_contexts"),
    )
    if call_chain_paths:
        preview = " -> ".join(call_chain_paths[:4])
        hints.append(f"已补充调用链上下文：{preview}，需要核对调用方入参、返回值和异常处理是否兼容")

    unchanged_callers = _collect_unchanged_caller_paths(
        focus_path=focus_path,
        context_payload=context_payload,
        repo_payload=repo_payload,
        changed_files=normalized_changed_files,
    )
    signature_change = _detect_signature_change(
        target_hunk_excerpt=target_hunk_excerpt,
        context_payload=context_payload,
    )
    if signature_change.get("detected"):
        signature_summary = str(signature_change.get("summary") or "").strip()
        if signature_summary:
            hints.append(f"检测到签名级变更：{signature_summary}，需要重点核对调用方入参、返回值或异常契约是否仍兼容")
        callsite_mismatch = _detect_callsite_mismatch(signature_change=signature_change, context_payload=context_payload)
        if callsite_mismatch:
            hints.append(callsite_mismatch)
    if unchanged_callers:
        preview = " / ".join(unchanged_callers[:3])
        suffix = " 等" if len(unchanged_callers) > 3 else ""
        if signature_change.get("detected"):
            hints.append(
                f"检测到 {len(unchanged_callers)} 个调用方未随这次签名变更一起修改：{preview}{suffix}，需要重点核对入参个数/类型、返回值和异常契约是否仍兼容"
            )
        else:
            hints.append(
                f"检测到 {len(unchanged_callers)} 个调用方未随本次改动一起修改：{preview}{suffix}，需要重点核对调用契约、返回值和异常语义是否仍兼容"
            )

    type_contract_paths = _collect_context_paths(
        context_payload,
        keys=("parent_contract_contexts", "domain_model_contexts"),
    )
    if type_contract_paths:
        preview = " / ".join(type_contract_paths[:4])
        hints.append(f"已补充类型契约上下文：{preview}，需要核对接口、领域模型或序列化假设是否被破坏")

    related_context_count = len(
        [
            item
            for item in list(context_payload.get("related_contexts") or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
    )
    if related_context_count > 0:
        hints.append(f"已补充 {related_context_count} 段关联源码上下文，可直接核对跨文件契约")

    symbol_contexts = list(context_payload.get("symbol_contexts") or repo_payload.get("symbol_contexts") or [])
    if symbol_contexts:
        first_symbol = next(
            (
                str(item.get("symbol") or "").strip()
                for item in symbol_contexts
                if isinstance(item, dict) and str(item.get("symbol") or "").strip()
            ),
            "",
        )
        definition_count = sum(
            len(list(item.get("definitions") or []))
            for item in symbol_contexts
            if isinstance(item, dict)
        )
        reference_count = sum(
            len(list(item.get("references") or []))
            for item in symbol_contexts
            if isinstance(item, dict)
        )
        if first_symbol and (definition_count > 0 or reference_count > 0):
            hints.append(
                f"符号 {first_symbol} 命中了 {definition_count} 处定义和 {reference_count} 处引用，需要检查调用链和类型假设是否一起被改坏"
            )

    search_matches = list(context_payload.get("search_matches") or repo_payload.get("matches") or [])
    match_paths = []
    seen_match_paths: set[str] = set()
    for item in search_matches[:6]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path or path == focus_path or path in seen_match_paths:
            continue
        seen_match_paths.add(path)
        match_paths.append(path)
    if match_paths:
        preview = " / ".join(match_paths[:3])
        suffix = " 等" if len(match_paths) > 3 else ""
        hints.append(f"代码仓检索还命中了关联实现：{preview}{suffix}")

    return hints[:4]


def _collect_context_paths(context_payload: dict[str, Any], *, keys: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for item in list(context_payload.get(key) or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def _collect_unchanged_caller_paths(
    *,
    focus_path: str,
    context_payload: dict[str, Any],
    repo_payload: dict[str, Any],
    changed_files: set[str],
) -> list[str]:
    if not changed_files:
        return []
    callers: list[str] = []
    seen: set[str] = set()
    for key in ("caller_contexts",):
        for item in list(context_payload.get(key) or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path or path == focus_path or path in seen:
                continue
            seen.add(path)
            callers.append(path)
    for symbol_context in list(context_payload.get("symbol_contexts") or repo_payload.get("symbol_contexts") or []):
        if not isinstance(symbol_context, dict):
            continue
        for item in list(symbol_context.get("references") or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path or path == focus_path or path in seen:
                continue
            seen.add(path)
            callers.append(path)
    return [path for path in callers if path not in changed_files]


def _detect_signature_change(
    *,
    target_hunk_excerpt: str,
    context_payload: dict[str, Any],
) -> dict[str, object]:
    diff_excerpt = str(target_hunk_excerpt or "").strip()
    if not diff_excerpt:
        diff_excerpt = str(context_payload.get("target_hunk_excerpt") or "").strip()
    if not diff_excerpt:
        target_hunk = context_payload.get("target_hunk")
        if isinstance(target_hunk, dict):
            diff_excerpt = str(target_hunk.get("excerpt") or "").strip()
    if not diff_excerpt:
        return {"detected": False}
    removed_signatures = _extract_signature_lines(diff_excerpt, prefix="-")
    added_signatures = _extract_signature_lines(diff_excerpt, prefix="+")
    if not removed_signatures and not added_signatures:
        return {"detected": False}

    summary_parts: list[str] = []
    if removed_signatures and added_signatures:
        before = removed_signatures[0]
        after = added_signatures[0]
        before_name = _extract_symbol_name(before)
        after_name = _extract_symbol_name(after)
        if before_name and after_name and before_name != after_name:
            summary_parts.append(f"方法名由 {before_name} 变为 {after_name}")
        before_param_count = _extract_parameter_count(before)
        after_param_count = _extract_parameter_count(after)
        if before_param_count != after_param_count:
            summary_parts.append(f"入参数量由 {before_param_count} 个变为 {after_param_count} 个")
        if _normalized_signature(before) != _normalized_signature(after):
            if not summary_parts:
                summary_parts.append("方法或构造器签名发生变化")
        before_return = _extract_return_type(before)
        after_return = _extract_return_type(after)
        if before_return and after_return and before_return != after_return:
            summary_parts.append(f"返回类型由 {before_return} 变为 {after_return}")
        before_throws = _extract_throws_clause(before)
        after_throws = _extract_throws_clause(after)
        if before_throws != after_throws and (before_throws or after_throws):
            summary_parts.append("抛出异常契约发生变化")
    elif added_signatures:
        summary_parts.append("新增了可被外部调用的方法或构造器签名")
    else:
        summary_parts.append("移除了原有的方法或构造器签名")

    return {
        "detected": bool(summary_parts),
        "summary": "；".join(summary_parts),
        "removed_signatures": removed_signatures,
        "added_signatures": added_signatures,
        "old_name": _extract_symbol_name(removed_signatures[0]) if removed_signatures else "",
        "new_name": _extract_symbol_name(added_signatures[0]) if added_signatures else "",
        "old_param_count": _extract_parameter_count(removed_signatures[0]) if removed_signatures else 0,
        "new_param_count": _extract_parameter_count(added_signatures[0]) if added_signatures else 0,
    }


def _extract_signature_lines(diff_excerpt: str, *, prefix: str) -> list[str]:
    signatures: list[str] = []
    for raw_line in str(diff_excerpt or "").splitlines():
        line = raw_line.rstrip()
        content = _strip_diff_marker(line, prefix=prefix)
        if not content:
            continue
        if _looks_like_signature_line(content):
            signatures.append(content.strip())
    return signatures


def _strip_diff_marker(line: str, *, prefix: str) -> str:
    normalized = str(line or "").rstrip()
    marker_index = normalized.find(f"{prefix} ")
    if marker_index >= 0:
        return normalized[marker_index + 2 :].strip()
    stripped = normalized.lstrip()
    if stripped.startswith(f"{prefix} "):
        return stripped[2:].strip()
    if stripped.startswith(prefix) and not stripped.startswith(("+++", "---")):
        return stripped[1:].strip()
    return ""


def _looks_like_signature_line(line: str) -> bool:
    stripped = str(line or "").strip().rstrip("{").rstrip(";").strip()
    if not stripped or "(" not in stripped or ")" not in stripped:
        return False
    if stripped.startswith(("if ", "for ", "while ", "switch ", "catch ", "return ", "@")):
        return False
    signature_markers = (
        "public ",
        "private ",
        "protected ",
        "internal ",
        "async ",
        "function ",
        "def ",
        "constructor(",
    )
    if any(stripped.startswith(marker) for marker in signature_markers):
        return True
    return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_<>]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", stripped))


def _extract_symbol_name(signature: str) -> str:
    normalized = str(signature or "").strip()
    if normalized.startswith("constructor("):
        return "constructor"
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", normalized)
    return str(match.group(1) if match else "").strip()


def _extract_parameter_count(signature: str) -> int:
    normalized = str(signature or "").strip()
    match = re.search(r"\((.*)\)", normalized)
    if not match:
        return 0
    raw_params = match.group(1).strip()
    if not raw_params:
        return 0
    return len([item for item in raw_params.split(",") if str(item).strip()])


def _extract_return_type(signature: str) -> str:
    normalized = str(signature or "").strip().rstrip("{").rstrip(";").strip()
    if normalized.startswith(("def ", "constructor(")):
        return ""
    match = re.search(
        r"(?:public|private|protected|internal|static|final|abstract|default|async|synchronized|native|strictfp|\s)+([A-Za-z0-9_<>\[\]\.?]+)\s+[A-Za-z_][A-Za-z0-9_]*\s*\(",
        normalized,
    )
    return str(match.group(1) if match else "").strip()


def _extract_throws_clause(signature: str) -> str:
    normalized = str(signature or "").strip()
    match = re.search(r"\bthrows\s+(.+)$", normalized)
    return str(match.group(1) if match else "").strip()


def _normalized_signature(signature: str) -> str:
    normalized = re.sub(r"\s+", " ", str(signature or "").strip())
    return normalized.rstrip("{").rstrip(";").strip()


def _detect_callsite_mismatch(
    *,
    signature_change: dict[str, object],
    context_payload: dict[str, Any],
) -> str:
    old_name = str(signature_change.get("old_name") or "").strip()
    new_name = str(signature_change.get("new_name") or "").strip()
    old_param_count = int(signature_change.get("old_param_count") or 0)
    new_param_count = int(signature_change.get("new_param_count") or 0)
    references = []
    for symbol_context in list(context_payload.get("symbol_contexts") or []):
        if not isinstance(symbol_context, dict):
            continue
        references.extend([item for item in list(symbol_context.get("references") or []) if isinstance(item, dict)])
    callers = [item for item in list(context_payload.get("caller_contexts") or []) if isinstance(item, dict)]
    candidates = references + callers
    mismatch_paths: list[str] = []
    seen: set[str] = set()
    target_names = [name for name in [old_name, new_name] if name]
    if not target_names:
        return ""
    for item in candidates:
        snippet = str(item.get("snippet") or "").strip()
        path = str(item.get("path") or "").strip()
        if not snippet or not path or path in seen:
            continue
        for target_name in target_names:
            call_arg_count = _extract_call_argument_count(snippet, target_name)
            if call_arg_count is None:
                continue
            if old_param_count != new_param_count and call_arg_count == old_param_count:
                seen.add(path)
                mismatch_paths.append(path)
                break
            if old_name and new_name and old_name != new_name and target_name == old_name:
                seen.add(path)
                mismatch_paths.append(path)
                break
    if not mismatch_paths:
        return ""
    preview = " / ".join(mismatch_paths[:3])
    suffix = " 等" if len(mismatch_paths) > 3 else ""
    return f"检测到 {len(mismatch_paths)} 个调用点仍保留旧调用形态：{preview}{suffix}，需要检查参数个数、方法名或返回值适配是否已同步"


def _extract_call_argument_count(snippet: str, symbol_name: str) -> int | None:
    normalized = str(snippet or "").strip()
    escaped = re.escape(str(symbol_name or "").strip())
    match = re.search(rf"(?:\.|\b){escaped}\s*\(([^)]*)\)", normalized)
    if not match:
        return None
    raw = str(match.group(1) or "").strip()
    if not raw:
        return 0
    return len([item for item in raw.split(",") if str(item).strip()])
