from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def run_expert_reviews(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "expert_review"
    next_state.setdefault("findings", [])
    return next_state
