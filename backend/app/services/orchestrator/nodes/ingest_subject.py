from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def ingest_subject(state: ReviewState) -> ReviewState:
    """把外部输入的 subject 装载为图执行初始状态。"""

    next_state = dict(state)
    next_state["phase"] = "ingest"
    return next_state
