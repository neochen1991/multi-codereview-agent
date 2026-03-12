from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def judge_and_merge(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "judge_and_merge"
    pending_human_issue_ids: list[str] = []
    merged_issues: list[dict[str, object]] = []
    for issue in next_state.get("issues", []):
        next_issue = dict(issue)
        if issue.get("needs_human"):
            next_issue["status"] = "needs_human"
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif (
            str(issue.get("severity") or "") in {"high", "critical", "blocker"}
            and not bool(issue.get("tool_verified"))
        ):
            next_issue["status"] = "needs_human"
            next_issue["resolution"] = "needs_human_review"
            next_issue["needs_human"] = True
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif not issue.get("verified") and float(issue.get("confidence") or 0.0) < 0.8:
            next_issue["status"] = "needs_human"
            next_issue["resolution"] = "needs_more_evidence"
            next_issue["needs_human"] = True
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif issue.get("status") == "debating":
            next_issue["status"] = "resolved"
            next_issue["resolution"] = "judge_accepted"
        else:
            next_issue["status"] = "resolved"
            next_issue["resolution"] = next_issue.get("resolution") or "judge_accepted"
        merged_issues.append(next_issue)
    next_state["issues"] = merged_issues
    next_state["pending_human_issue_ids"] = pending_human_issue_ids
    return next_state
