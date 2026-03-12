from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def human_gate(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "human_gate"
    pending_issue_ids = list(next_state.get("pending_human_issue_ids", []))
    next_state["human_review_required"] = bool(pending_issue_ids)
    return next_state
