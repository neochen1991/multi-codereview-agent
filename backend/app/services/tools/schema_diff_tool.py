from __future__ import annotations

from typing import Any


def schema_diff_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """检查变更中是否包含 migration/schema 类数据库改动。"""

    changed_files = " ".join(str(item) for item in payload.get("changed_files", [])).lower()
    verified = any(token in changed_files for token in ["migration", ".sql", "schema"])
    return {
        "verified": verified,
        "score": 0.93 if verified else 0.4,
        "summary": "Schema diff tool checked migration-like changes.",
    }
