from __future__ import annotations

from typing import Any


def local_diff_tool(payload: dict[str, Any]) -> dict[str, Any]:
    changed_files = [str(item) for item in payload.get("changed_files", [])]
    return {
        "verified": bool(changed_files),
        "score": 0.82 if changed_files else 0.2,
        "summary": f"Local diff inspected for {len(changed_files)} files.",
        "changed_files": changed_files,
    }
