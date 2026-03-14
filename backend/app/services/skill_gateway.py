from __future__ import annotations

from app.services.tool_gateway import ReviewToolGateway

# Backward-compatible alias for older imports/tests/history.
SkillGateway = ReviewToolGateway

__all__ = ["ReviewToolGateway", "SkillGateway"]
