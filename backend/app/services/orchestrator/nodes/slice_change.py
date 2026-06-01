from __future__ import annotations

import re

from app.services.change_understanding_service import ChangeUnderstandingService
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.orchestrator.state import ReviewState


STATIC_DIFF_RISK_TOKENS = (
    "loop_call_amplification",
    "n_plus_one",
    "循环",
    "逐条",
    "query_bound_removed",
    "query_boundary_missing",
    "unbounded_query_risk",
    "limit",
    "分页",
    "全量",
    "security_guard_removed",
    "input_validation_removed",
    "missing_auth_check",
    "@valid",
    "权限",
    "鉴权",
    "校验",
    "idempotency_guard_removed",
    "duplicate_processing_risk",
    "幂等",
    "重复",
    "lock_guard_removed",
    "concurrency_guard_removed",
    "锁",
    "并发",
    "comment_contract_unimplemented",
    "注释",
    "todo",
    "未实现",
    "exception_swallowed",
    "吞异常",
)


def slice_change(state: ReviewState) -> ReviewState:
    """把 changed_files 切成后续可路由的 hunk 级 change slice。"""

    next_state = dict(state)
    next_state["phase"] = "slice_change"
    files = list(next_state.get("changed_files", []))
    unified_diff = str(next_state.get("unified_diff") or "")
    diff_service = DiffExcerptService()
    change_understanding = ChangeUnderstandingService(diff_service).understand(
        changed_files=[str(item).strip() for item in files if str(item).strip()],
        unified_diff=unified_diff,
    )
    change_slices: list[dict[str, object]] = []
    risk_hints = list(next_state.get("risk_hints") or [])
    for file_path in files:
        normalized_file_path = str(file_path or "").strip()
        if not normalized_file_path:
            continue
        hunks = diff_service.list_hunks(unified_diff, normalized_file_path)
        if not hunks:
            change_slices.append(_build_file_slice(len(change_slices) + 1, normalized_file_path))
            continue
        for hunk_index, hunk in enumerate(hunks, start=1):
            risk_signals = _detect_hunk_risk_signals(normalized_file_path, str(hunk.get("excerpt") or ""))
            for hint in _risk_hints_for_signals(risk_signals):
                if hint not in risk_hints:
                    risk_hints.append(hint)
            change_slices.append(
                {
                    "slice_id": f"slice_{len(change_slices) + 1}",
                    "file_path": normalized_file_path,
                    "module": normalized_file_path.split("/")[0] if "/" in normalized_file_path else "root",
                    "hunk_index": hunk_index,
                    "line_start": int(hunk.get("start_line") or 1),
                    "line_end": int(hunk.get("end_line") or hunk.get("start_line") or 1),
                    "changed_lines": [
                        int(item)
                        for item in list(hunk.get("changed_lines") or [])
                        if isinstance(item, int)
                    ],
                    "hunk_header": str(hunk.get("hunk_header") or ""),
                    "excerpt": str(hunk.get("excerpt") or ""),
                    "summary": _build_slice_summary(risk_signals),
                    "risk_signals": risk_signals,
                }
            )
    next_state["change_slices"] = change_slices
    next_state["risk_hints"] = risk_hints
    next_state["change_understanding"] = change_understanding
    next_state["risk_domains"] = list(change_understanding.get("risk_domains") or [])
    next_state["expert_hints"] = list(change_understanding.get("expert_hints") or [])
    return next_state


def _build_file_slice(index: int, file_path: str) -> dict[str, object]:
    return {
        "slice_id": f"slice_{index}",
        "file_path": file_path,
        "module": file_path.split("/")[0] if "/" in file_path else "root",
        "risk_signals": _detect_hunk_risk_signals(file_path, file_path),
        "summary": "未解析到 hunk，按文件级变更兜底派工。",
    }


def _build_slice_summary(risk_signals: list[str]) -> str:
    if not risk_signals:
        return "普通代码变更 hunk。"
    labels = {
        "security_guard_removed": "删除安全或入口校验",
        "query_bound_removed": "删除分页或查询边界",
        "loop_call_amplification": "循环内外部调用放大",
        "comment_contract_unimplemented": "注释/TODO 承诺未实现",
        "exception_swallowed": "异常处理被削弱",
        "lock_scope_risk": "锁或并发控制风险",
        "transactional_side_effect": "事务内外部副作用",
        "mq_delivery_risk": "消息可靠性风险",
        "cache_consistency_risk": "缓存一致性风险",
    }
    return "；".join(labels.get(signal, signal) for signal in risk_signals[:6])


def _risk_hints_for_signals(risk_signals: list[str]) -> list[str]:
    hints: list[str] = []
    signal_set = set(risk_signals)
    if signal_set & {"security_guard_removed"}:
        hints.append("security_surface")
    if signal_set & {"query_bound_removed", "loop_call_amplification"}:
        hints.append("database_migration")
    if signal_set & {"lock_scope_risk", "transactional_side_effect"}:
        hints.append("performance_reliability")
    return hints


def _detect_hunk_risk_signals(file_path: str, excerpt: str) -> list[str]:
    text = f"{file_path}\n{excerpt}"
    lowered = text.lower()
    signals: list[str] = []

    removed_lines = _diff_lines(excerpt, "-")
    added_lines = _diff_lines(excerpt, "+")
    added_blob = "\n".join(added_lines).lower()
    removed_blob = "\n".join(removed_lines).lower()

    security_tokens = ["@valid", "permission", "authorize", "auth", "csrf", "sanitize", "tenant", "ownerid", "mismatch", "rejectvalue"]
    if any(token in removed_blob and token not in added_blob for token in security_tokens):
        signals.append("security_guard_removed")
    if any(token in removed_blob for token in [" limit ", "pageable", "page<", "setmaxresults", "chunk", "chunks", "分页"]):
        signals.append("query_bound_removed")
    if _has_loop_external_call(added_blob):
        signals.append("loop_call_amplification")
    if _has_comment_contract_gap(added_blob):
        signals.append("comment_contract_unimplemented")
    if any(token in added_blob for token in ["catch", "except", "printstacktrace", "return null", "return true", "pass"]) and _looks_like_swallowed_exception(added_blob, removed_blob):
        signals.append("exception_swallowed")
    if any(token in lowered for token in [" synchronized", "lock(", "trylock", "redisson", "setnx", "mutex"]):
        signals.append("lock_scope_risk")
    if any(token in lowered for token in ["@transactional", "begintransaction", "commit("]) and any(token in lowered for token in ["http", "client.", "send(", "publish(", "eventbus", "mq", "kafka"]):
        signals.append("transactional_side_effect")
    if any(token in lowered for token in ["kafka", "rabbit", "rocketmq", "ack", "nack", "deadletter", "producer", "consumer"]):
        signals.append("mq_delivery_risk")
    if any(token in lowered for token in ["redis", "cache", "ttl", "expire", "setnx"]):
        signals.append("cache_consistency_risk")
    return list(dict.fromkeys(signals))


def _diff_lines(excerpt: str, marker: str) -> list[str]:
    needle = f"| {marker}"
    deleted_needle = f"{marker} |"
    result: list[str] = []
    for raw_line in str(excerpt or "").splitlines():
        stripped = raw_line.lstrip()
        if stripped.startswith(("---", "+++")):
            continue
        if deleted_needle in stripped:
            result.append(stripped.split(deleted_needle, 1)[1].strip())
            continue
        if needle not in raw_line:
            continue
        result.append(raw_line.split(needle, 1)[1].strip())
    return result


def _has_loop_external_call(text: str) -> bool:
    for match in re.finditer(r"\b(for|while|foreach|map|filter|stream\(\)|forEach)\b", text, re.I):
        window = text[match.start() : match.start() + 700]
        if re.search(r"\b\w*(repository|mapper|dao|client|gateway|service)\w*\s*\.|\b(http|fetch|axios|send|publish)\b", window, re.I):
            return True
    return False


def _has_comment_contract_gap(text: str) -> bool:
    if not any(token in text for token in ["todo", "fixme", "待实现", "未实现", "后续", "发送", "校验", "缓存", "重试"]):
        return False
    has_comment = any(line.strip().startswith(("//", "#", "*")) or "todo" in line.lower() for line in text.splitlines())
    has_real_impl = any(token in text for token in ["publish", "send", "validate", "check", "retry", "cache", "redis", "notify", "audit"])
    return has_comment and not has_real_impl


def _looks_like_swallowed_exception(added_blob: str, removed_blob: str) -> bool:
    if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", added_blob, re.S):
        return True
    if "printstacktrace" in removed_blob and "printstacktrace" not in added_blob:
        return True
    return any(token in added_blob for token in ["return null", "return true", "pass", "logger.debug"])
