from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def expand_context(state: ReviewState) -> ReviewState:
    """从变更文件名中提取高层风险提示，供后续路由使用。"""

    next_state = dict(state)
    next_state["phase"] = "expand_context"
    files = [str(item).lower() for item in next_state.get("changed_files", [])]
    risk_hints: list[str] = []
    if any(token in path for path in files for token in ["migration", ".sql", "schema"]):
        risk_hints.append("database_migration")
    if any(token in path for path in files for token in ["auth", "security", "permission"]):
        risk_hints.append("security_surface")
    if any(token in path for path in files for token in ["api", "contract", "controller"]):
        risk_hints.append("api_contract")
    next_state["risk_hints"] = risk_hints
    return next_state
