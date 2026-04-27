from __future__ import annotations

import re
from typing import Any


def static_diff_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """从 diff 中提取高置信静态质量信号，作为 LLM finding 的证据锚点。"""

    unified_diff = str(payload.get("unified_diff") or "")
    issue = dict(payload.get("issue") or {})
    signals = _detect_static_signals(unified_diff, issue)
    return {
        "verified": bool(signals),
        "score": 0.88 if signals else 0.2,
        "summary": "Static diff signals: " + (", ".join(signals) if signals else "none"),
        "signals": signals,
    }


def _detect_static_signals(unified_diff: str, issue: dict[str, Any]) -> list[str]:
    text = str(unified_diff or "")
    issue_type = str(issue.get("normalized_issue_type") or "").strip().lower()
    issue_text = "\n".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("summary") or ""),
            str(issue.get("claim") or ""),
            *[str(item) for item in list(issue.get("evidence") or [])],
        ]
    ).lower()
    signals: list[str] = []
    if _has_loop_call_amplification(text) and (
        issue_type in {"loop_call_amplification", "n_plus_one_query", "performance_loop_call"}
        or any(token in issue_text for token in ["循环", "loop", "逐条", "n+1", "repository", "外部调用"])
    ):
        signals.append("loop_call_amplification")
    if _has_query_bound_removed(text) and (
        issue_type in {"query_bound_removed", "unbounded_query_risk", "query_boundary_missing"}
        or any(token in issue_text for token in ["limit", "分页", "pageable", "全量", "无界", "unbounded"])
    ):
        signals.append("query_bound_removed")
    if _has_security_guard_removed(text) and (
        issue_type in {"security_guard_removed", "missing_auth_check", "input_validation_removed"}
        or any(token in issue_text for token in ["权限", "鉴权", "@valid", "校验", "validation", "guard", "auth"])
    ):
        signals.append("security_guard_removed")
    if _has_idempotency_guard_removed(text) and (
        issue_type in {"idempotency_guard_removed", "duplicate_processing_risk"}
        or any(token in issue_text for token in ["幂等", "重复", "idempotent", "duplicate"])
    ):
        signals.append("idempotency_guard_removed")
    if _has_lock_guard_removed(text) and (
        issue_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}
        or any(token in issue_text for token in ["锁", "并发", "lock", "synchronized", "setnx"])
    ):
        signals.append("lock_guard_removed")
    if _has_comment_contract_unimplemented(text) and (
        issue_type in {"comment_contract_unimplemented", "contract_unimplemented"}
        or any(token in issue_text for token in ["注释", "todo", "fixme", "未实现", "contract"])
    ):
        signals.append("comment_contract_unimplemented")
    if _has_swallowed_exception(text) and (
        issue_type in {"exception_swallowed", "exception_semantics_weakened"}
        or any(token in issue_text for token in ["异常", "exception", "吞"])
    ):
        signals.append("exception_swallowed")
    return signals


def _added_lines(unified_diff: str) -> list[str]:
    lines: list[str] = []
    for line in str(unified_diff or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("+++") or not stripped.startswith("+"):
            continue
        lines.append(stripped[1:].strip())
    return lines


def _removed_lines(unified_diff: str) -> list[str]:
    lines: list[str] = []
    for line in str(unified_diff or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("---") or not stripped.startswith("-"):
            continue
        lines.append(stripped[1:].strip())
    return lines


def _has_loop_call_amplification(unified_diff: str) -> bool:
    added = _added_lines(unified_diff)
    for index, line in enumerate(added):
        if not re.search(r"\b(for|while)\b|\.forEach\s*\(", line):
            continue
        window = "\n".join(added[index : index + 8])
        if re.search(r"\b(repository|mapper|dao|client|service)\s*\.\s*\w+\s*\(", window, re.I):
            return True
        if re.search(r"\b(find|query|select|execute|send|publish|call)\w*\s*\(", window, re.I):
            return True
    return False


def _has_query_bound_removed(unified_diff: str) -> bool:
    removed_blob = "\n".join(_removed_lines(unified_diff)).lower()
    added_blob = "\n".join(_added_lines(unified_diff)).lower()
    boundary_tokens = [" limit ", "limit :", "pageable", "page<", "setmaxresults", "chunk", "chunks", "分页"]
    if not any(token in removed_blob for token in boundary_tokens):
        return False
    return not any(token in added_blob for token in boundary_tokens)


def _has_security_guard_removed(unified_diff: str) -> bool:
    removed_blob = "\n".join(_removed_lines(unified_diff)).lower()
    added_blob = "\n".join(_added_lines(unified_diff)).lower()
    guard_tokens = [
        "@valid",
        "bindingresult",
        "permission",
        "haspermission",
        "authorize",
        "authenticated",
        "csrf",
        "sanitize",
        "ownerid",
        "tenant",
        "mismatch",
        "rejectvalue",
    ]
    removed_guard_tokens = [token for token in guard_tokens if token in removed_blob and token not in added_blob]
    if not removed_guard_tokens:
        return False
    return True


def _has_idempotency_guard_removed(unified_diff: str) -> bool:
    removed_blob = "\n".join(_removed_lines(unified_diff)).lower()
    added_blob = "\n".join(_added_lines(unified_diff)).lower()
    tokens = ["idempotent", "idempotency", "deduplicate", "duplicate", "requestid", "eventid", "幂等", "重复消费"]
    return any(token in removed_blob for token in tokens) and not any(token in added_blob for token in tokens)


def _has_lock_guard_removed(unified_diff: str) -> bool:
    removed_blob = "\n".join(_removed_lines(unified_diff)).lower()
    added_blob = "\n".join(_added_lines(unified_diff)).lower()
    tokens = ["synchronized", "lock(", "trylock", "unlock", "setnx", "redisson", "mutex", "锁"]
    return any(token in removed_blob for token in tokens) and not any(token in added_blob for token in tokens)


def _has_comment_contract_unimplemented(unified_diff: str) -> bool:
    added = _added_lines(unified_diff)
    for index, line in enumerate(added):
        lowered = line.lower()
        if not any(token in lowered for token in ["todo", "fixme", "hack", "后续", "待实现", "未实现", "发送", "同步", "校验", "审计"]):
            continue
        window = "\n".join(added[index : index + 5]).lower()
        implementation_tokens = [
            "throw",
            "return",
            "implements",
            "override",
            "=",
            "publish",
            "send",
            "notify",
            "audit",
            "validate",
            "check",
            "event",
        ]
        if not any(token in window for token in implementation_tokens):
            return True
    return False


def _has_swallowed_exception(unified_diff: str) -> bool:
    added = _added_lines(unified_diff)
    for index, line in enumerate(added):
        if not re.search(r"\bcatch\s*\(|\bexcept\b", line):
            continue
        window = "\n".join(added[index : index + 6]).lower()
        if re.search(r"\bpass\b|//\s*(ignore|todo)|/\*\s*(ignore|todo)", window):
            return True
        if "catch" in window and not any(token in window for token in ["throw", "raise", "return", "log.", "logger."]):
            return True
    return False
