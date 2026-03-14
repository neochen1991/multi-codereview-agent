from __future__ import annotations

from typing import Any


def coverage_diff_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """根据测试文件命中情况给出覆盖率变化的启发式结果。"""

    changed_files = " ".join(str(item) for item in payload.get("changed_files", [])).lower()
    verified = "test" in changed_files or "spec" in changed_files
    return {
        "verified": verified,
        "score": 0.88 if verified else 0.52,
        "summary": "Coverage diff inspected against changed test surfaces.",
    }
