from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def persist_feedback(state: ReviewState) -> ReviewState:
    """为后续反馈学习阶段保留统一的状态出口。"""

    next_state = dict(state)
    next_state["phase"] = "persist_feedback"
    next_state.setdefault("feedback_labels", [])
    return next_state
