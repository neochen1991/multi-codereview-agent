from __future__ import annotations

from app.services.evidence_verifier_service import EvidenceVerifierService
from app.services.orchestrator.state import ReviewState


SPECULATIVE_TOKENS = (
    "可能",
    "也许",
    "或许",
    "需要确认",
    "需确认",
    "需要进一步确认",
    "需要结合",
    "取决于",
    "如果",
    "假设",
    "不确定",
    "建议检查",
    "needs confirmation",
    "need to confirm",
    "if ",
    "may ",
    "might ",
    "could ",
    "depends on",
)

DIRECT_ANCHOR_TOKENS = (
    "line",
    "行",
    "diff",
    "hunk",
    "新增",
    "删除",
    "调用",
    "返回",
    "赋值",
    "直接构造",
    "直接调用",
    "绕过",
    "+",
    "-",
    "(",
    ")",
    ".",
)


def evidence_verification(state: ReviewState) -> ReviewState:
    """为每个 issue 选择 verifier，并补齐 verified/tool 信息。"""

    next_state = dict(state)
    next_state["phase"] = "evidence_verification"
    verifier = EvidenceVerifierService()
    verified_issues: list[dict[str, object]] = []
    risk_hints = set(next_state.get("risk_hints", []))
    for issue in next_state.get("issues", []):
        strategy = _pick_verification_strategy(issue)
        if _should_use_static_diff(issue, next_state):
            strategy = "static_diff"
        topic = str(issue.get("topic", ""))
        verification_result = verifier.verify(
            issue_id=str(issue.get("issue_id", "")),
            strategy=strategy,
            payload={
                "changed_files": next_state.get("changed_files", []),
                "unified_diff": str(next_state.get("unified_diff") or ""),
                "issue": dict(issue),
            },
        )
        verified = bool(verification_result.get("tool_verified"))
        confidence = float(issue.get("confidence", 0.0))
        if verified:
            confidence = min(0.98, round(max(confidence, float(verification_result.get("score", 0.0))), 2))
        evidence_quality = _assess_evidence_quality(issue, next_state, verification_result)
        if evidence_quality["false_positive_risk"] == "high":
            verified = False
            confidence = min(confidence, 0.49)
        elif evidence_quality["false_positive_risk"] == "medium" and not verified:
            confidence = min(confidence, 0.69)
        needs_human = False
        severity = str(issue.get("severity", "medium"))
        if topic == "security" and "security_surface" in risk_hints:
            needs_human = True
        if topic == "database" and "database_migration" in risk_hints:
            needs_human = True
        if severity in {"blocker", "critical"}:
            needs_human = True
        next_issue = dict(issue)
        next_issue["verified"] = verified
        next_issue["confidence"] = confidence
        next_issue["needs_human"] = needs_human
        next_issue["verifier_name"] = "builtin_verifier"
        next_issue["tool_name"] = verification_result["tool_name"]
        next_issue["tool_verified"] = verification_result["tool_verified"]
        next_issue["evidence_quality"] = evidence_quality
        static_signals = list(evidence_quality.get("static_analysis_signals") or [])
        if static_signals:
            next_issue["direct_evidence"] = True
            next_issue.setdefault("evidence", [])
            evidence_items = [str(item) for item in list(next_issue.get("evidence") or []) if str(item).strip()]
            for signal in static_signals:
                marker = f"静态 diff 信号命中: {signal}"
                if marker not in evidence_items:
                    evidence_items.append(marker)
            next_issue["evidence"] = evidence_items
        verified_issues.append(next_issue)
    next_state["issues"] = verified_issues
    return next_state


def _pick_verification_strategy(issue: dict[str, object]) -> str:
    """根据 issue 类型、文件和参与专家选择最合适的 verifier。"""

    finding_type = str(issue.get("finding_type") or "risk_hypothesis")
    file_path = str(issue.get("file_path") or "").lower()
    topic = str(issue.get("topic") or "").lower()
    participants = [str(item).lower() for item in list(issue.get("participant_expert_ids") or []) if str(item).strip()]
    evidence_tags = {str(item).lower() for item in issue.get("evidence", []) if str(item).strip()}

    if (
        finding_type == "test_gap"
        or "test_surface" in evidence_tags
        or any(token in f"{file_path} {topic}" for token in ["test", "spec", "jest", "vitest", "playwright"])
    ):
        return "coverage_diff"

    if any(
        token in f"{file_path} {topic}"
        for token in ["migration", ".sql", "schema", "repository", "db"]
    ) or "database_migration" in evidence_tags or any(
        expert in {"database_analysis", "performance_reliability"} for expert in participants
    ):
        return "schema_diff"

    return "local_diff"


def _should_use_static_diff(issue: dict[str, object], state: ReviewState) -> bool:
    if not str(state.get("unified_diff") or "").strip():
        return False
    issue_type = str(issue.get("normalized_issue_type") or "").strip().lower()
    text = "\n".join(
        [
            issue_type,
            str(issue.get("title") or ""),
            str(issue.get("summary") or ""),
            *[str(item) for item in list(issue.get("evidence") or [])],
        ]
    ).lower()
    return any(
        token in text
        for token in [
            "loop_call_amplification",
            "n_plus_one",
            "循环",
            "逐条",
            "comment_contract_unimplemented",
            "注释",
            "todo",
            "未实现",
            "exception_swallowed",
            "吞异常",
        ]
    )


def _assess_evidence_quality(
    issue: dict[str, object],
    state: ReviewState,
    verification_result: dict[str, object],
) -> dict[str, object]:
    """识别只靠猜测或外部条件成立的问题，避免弱证据被误当成确定 issue。"""

    evidence = [str(item).strip() for item in list(issue.get("evidence") or []) if str(item).strip()]
    cross_file_evidence = [
        str(item).strip() for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()
    ]
    context_files = [str(item).strip() for item in list(issue.get("context_files") or []) if str(item).strip()]
    text_blob = "\n".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("summary") or ""),
            str(issue.get("claim") or ""),
            str(issue.get("description") or ""),
            *evidence,
            *cross_file_evidence,
        ]
    ).lower()
    finding_type = str(issue.get("finding_type") or "risk_hypothesis")
    direct_evidence = bool(issue.get("direct_evidence"))
    has_file_anchor = bool(str(issue.get("file_path") or "").strip()) or bool(context_files)
    has_line_anchor = any(
        issue.get(key) not in (None, "", 0)
        for key in ("line", "line_start", "line_number", "start_line", "end_line")
    )
    has_direct_text_anchor = any(any(token in item.lower() for token in DIRECT_ANCHOR_TOKENS) for item in evidence)
    has_substantial_evidence = len(evidence) + len(cross_file_evidence) >= 2 or any(len(item) >= 24 for item in evidence)
    tool_verified = bool(verification_result.get("tool_verified"))
    static_signals = [str(item).strip() for item in list(verification_result.get("details", {}).get("signals") or []) if str(item).strip()]
    changed_files = [str(item).strip() for item in state.get("changed_files", []) if str(item).strip()]
    speculative = any(token in text_blob for token in SPECULATIVE_TOKENS)

    reasons: list[str] = []
    if direct_evidence:
        reasons.append("direct_evidence")
    if tool_verified:
        reasons.append("tool_verified")
    if has_file_anchor:
        reasons.append("file_anchor")
    if has_line_anchor:
        reasons.append("line_anchor")
    if has_direct_text_anchor:
        reasons.append("text_anchor")
    if has_substantial_evidence:
        reasons.append("substantial_evidence")
    if speculative:
        reasons.append("speculative_language")

    anchored = has_file_anchor and (has_line_anchor or has_direct_text_anchor or has_substantial_evidence)
    if static_signals:
        anchored = True
        direct_evidence = True
        risk = "low"
        reasons.append("static_analysis_signal")
    elif direct_evidence and anchored:
        risk = "low"
    elif direct_evidence and tool_verified:
        risk = "low"
    elif speculative and not direct_evidence and not anchored:
        risk = "high"
    elif finding_type in {"risk_hypothesis", "design_concern"} and not direct_evidence and not anchored:
        risk = "high"
    elif speculative and not direct_evidence:
        risk = "medium"
    elif not evidence and not cross_file_evidence and changed_files:
        risk = "medium"
    else:
        risk = "low"

    return {
        "false_positive_risk": risk,
        "speculative_language": speculative,
        "anchored": anchored,
        "static_analysis_signals": static_signals,
        "reasons": reasons,
    }
