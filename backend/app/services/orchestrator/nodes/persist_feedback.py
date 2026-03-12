from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def persist_feedback(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "persist_feedback"
    next_state.setdefault("feedback_labels", [])
    return next_state
