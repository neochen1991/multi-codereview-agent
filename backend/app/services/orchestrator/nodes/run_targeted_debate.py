from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def run_targeted_debate(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "run_targeted_debate"
    issues: list[dict[str, object]] = []
    for conflict in next_state.get("conflicts", []):
        issue = dict(conflict)
        participant_count = len(issue.get("participant_expert_ids", []))
        confidence = float(issue.get("confidence", 0.0))
        needs_debate = participant_count > 1 or confidence < 0.8
        issue["needs_debate"] = needs_debate
        issue["status"] = "debating" if needs_debate else "open"
        issue["summary"] = (
            f"{issue.get('summary', '')} 已组织 {max(participant_count, 1)} 个专家进行定向辩论。"
        ).strip()
        issues.append(issue)
    next_state["issues"] = issues
    return next_state
