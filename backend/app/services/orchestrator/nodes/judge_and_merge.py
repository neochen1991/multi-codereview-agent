from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def judge_and_merge(state: ReviewState) -> ReviewState:
    """依据证据强度和风险等级决定 issue 的最终状态。"""

    next_state = dict(state)
    next_state["phase"] = "judge_and_merge"
    pending_human_issue_ids: list[str] = []
    merged_issues: list[dict[str, object]] = []
    for issue in next_state.get("issues", []):
        next_issue = dict(issue)
        text_blob = "\n".join(
            [
                str(issue.get("title") or ""),
                str(issue.get("summary") or ""),
                str(issue.get("claim") or ""),
                *[str(item) for item in list(issue.get("evidence") or [])],
            ]
        ).lower()
        if any(
            token in text_blob
            for token in [
                "无风险",
                "没有风险",
                "无架构风险",
                "无可维护性风险",
                "代码格式化",
                "缩进调整",
                "仅涉及格式化",
                "whitespace",
                "format only",
            ]
        ):
            continue
        finding_type = str(issue.get("finding_type") or "risk_hypothesis")
        direct_evidence = bool(issue.get("direct_evidence"))
        tool_verified = bool(issue.get("tool_verified"))
        verified = bool(issue.get("verified"))
        severity = str(issue.get("severity") or "")
        confidence = float(issue.get("confidence") or 0.0)
        cross_file_evidence = [
            str(item).strip() for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()
        ]
        context_files = [
            str(item).strip() for item in list(issue.get("context_files") or []) if str(item).strip()
        ]
        evidence = [
            str(item).strip() for item in list(issue.get("evidence") or []) if str(item).strip()
        ]
        assumptions = [
            str(item).strip() for item in list(issue.get("assumptions") or []) if str(item).strip()
        ]
        evidence_strength = len(cross_file_evidence) + len(context_files) + len(evidence)
        speculative_issue = bool(assumptions) and not direct_evidence
        if issue.get("needs_human"):
            next_issue["status"] = "needs_human"
            next_issue["resolution"] = next_issue.get("resolution") or "needs_human_review"
            next_issue["needs_human"] = True
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif finding_type == "design_concern":
            next_issue["status"] = "comment"
            next_issue["resolution"] = "comment"
            next_issue["needs_human"] = False
        elif finding_type == "risk_hypothesis" and not direct_evidence:
            if speculative_issue and confidence <= 0.5:
                next_issue["status"] = "needs_verification"
                next_issue["resolution"] = "needs_verification"
                next_issue["needs_human"] = False
            elif (tool_verified or verified) and evidence_strength >= 4:
                next_issue["status"] = "resolved"
                next_issue["resolution"] = next_issue.get("resolution") or "accepted_with_verification"
                next_issue["needs_human"] = False
            else:
                next_issue["status"] = "needs_verification"
                next_issue["resolution"] = "needs_verification"
                next_issue["needs_human"] = False
        elif (
            severity in {"high", "critical", "blocker"}
            and not tool_verified
        ):
            next_issue["status"] = "needs_human"
            next_issue["resolution"] = "needs_human_review"
            next_issue["needs_human"] = True
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif not verified and confidence < 0.8:
            next_issue["status"] = "needs_human"
            next_issue["resolution"] = "needs_more_evidence"
            next_issue["needs_human"] = True
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif issue.get("status") == "debating":
            next_issue["status"] = "resolved"
            next_issue["resolution"] = "accepted"
        else:
            next_issue["status"] = "resolved"
            next_issue["resolution"] = next_issue.get("resolution") or "accepted"
        merged_issues.append(next_issue)
    next_state["issues"] = merged_issues
    next_state["pending_human_issue_ids"] = pending_human_issue_ids
    return next_state
