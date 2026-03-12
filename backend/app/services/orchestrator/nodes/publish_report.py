from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def publish_report(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "publish_report"
    findings = list(next_state.get("findings", []))
    issues = list(next_state.get("issues", []))
    pending_count = len(next_state.get("pending_human_issue_ids", []))
    next_state["report_summary"] = (
        f"审核报告已生成，共收敛 {len(findings)} 条 findings，"
        f"形成 {len(issues)} 个议题，其中 {pending_count} 个待人工裁决。"
    )
    return next_state
