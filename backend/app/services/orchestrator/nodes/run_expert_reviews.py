from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def run_expert_reviews(state: ReviewState) -> ReviewState:
    """占位更新状态，表示图执行进入专家审查阶段。"""

    next_state = dict(state)
    next_state["phase"] = "expert_review"
    next_state.setdefault("findings", [])
    return next_state
