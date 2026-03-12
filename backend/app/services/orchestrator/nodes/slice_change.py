from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def slice_change(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "slice_change"
    files = list(next_state.get("changed_files", []))
    next_state["change_slices"] = [
        {
            "slice_id": f"slice_{index + 1}",
            "file_path": file_path,
            "module": file_path.split("/")[0] if "/" in file_path else "root",
        }
        for index, file_path in enumerate(files)
    ]
    return next_state
