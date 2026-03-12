from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def ingest_subject(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "ingest"
    return next_state
