from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def route_experts(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "route_experts"
    selected = list(next_state.get("selected_experts", []))
    risk_hints = set(next_state.get("risk_hints", []))
    if "security_surface" in risk_hints and "security_compliance" not in selected:
        selected.append("security_compliance")
    if "database_migration" in risk_hints and "performance_reliability" not in selected:
        selected.append("performance_reliability")
    next_state["selected_experts"] = selected
    return next_state
