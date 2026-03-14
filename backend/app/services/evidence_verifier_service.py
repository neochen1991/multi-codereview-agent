from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.capability_gateway import CapabilityGateway
from app.services.tools.coverage_diff_tool import coverage_diff_tool
from app.services.tools.local_diff_tool import local_diff_tool
from app.services.tools.schema_diff_tool import schema_diff_tool


class EvidenceVerifierService:
    """为 issue 选择并执行内建 verifier 工具。"""

    def __init__(self, gateway: CapabilityGateway | None = None) -> None:
        """初始化能力网关，并确保默认工具已注册。"""

        self.gateway = gateway or CapabilityGateway()
        self._ensure_default_tools()

    def verify(self, issue_id: str, strategy: str, payload: dict[str, Any]) -> dict[str, Any]:
        """执行指定 verifier，并返回统一格式的核验结果。"""

        result = self.gateway.invoke(strategy, payload)
        score = float(result.get("score", 0.0))
        return {
            "issue_id": issue_id,
            "tool_name": strategy,
            "tool_verified": bool(result.get("verified", False)),
            "score": score,
            "summary": result.get("summary", ""),
            "details": result,
        }

    def _ensure_default_tools(self) -> None:
        """注册系统内建的 verifier 工具。"""

        for name, tool in {
            "local_diff": local_diff_tool,
            "coverage_diff": coverage_diff_tool,
            "schema_diff": schema_diff_tool,
        }.items():
            if not self.gateway.has_tool(name):
                self.gateway.register_tool(name, tool)
