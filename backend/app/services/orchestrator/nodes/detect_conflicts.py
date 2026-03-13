from __future__ import annotations

from app.services.orchestrator.state import ReviewState


def detect_conflicts(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "detect_conflicts"
    findings = list(next_state.get("findings", []))
    grouped: dict[str, list[dict[str, object]]] = {}
    for finding in findings:
        file_path = str(finding.get("file_path", "")).strip() or "unknown"
        line_start = int(finding.get("line_start", 1) or 1)
        line_bucket = max(1, ((line_start - 1) // 25) + 1)
        key = f"{file_path}::{line_bucket}"
        grouped.setdefault(key, []).append(finding)
    conflicts: list[dict[str, object]] = []
    for key, items in grouped.items():
        first = items[0]
        highest_severity = "medium"
        if any(str(item.get("severity")) in {"critical", "high"} for item in items):
            highest_severity = "high"
        if any(str(item.get("severity")) == "blocker" for item in items):
            highest_severity = "blocker"
        conflicts.append(
            {
                "issue_id": first.get("finding_id"),
                "topic": key,
                "title": first.get("title"),
                "summary": first.get("summary"),
                "finding_type": first.get("finding_type", "risk_hypothesis"),
                "file_path": first.get("file_path"),
                "line_start": first.get("line_start"),
                "finding_ids": [item.get("finding_id") for item in items],
                "participant_expert_ids": [item.get("expert_id") for item in items],
                "evidence": [e for item in items for e in item.get("evidence", [])],
                "cross_file_evidence": [e for item in items for e in item.get("cross_file_evidence", [])],
                "assumptions": [e for item in items for e in item.get("assumptions", [])],
                "context_files": [e for item in items for e in item.get("context_files", [])],
                "direct_evidence": any(str(item.get("finding_type")) == "direct_defect" for item in items),
                "severity": highest_severity,
                "confidence": round(
                    sum(float(item.get("confidence", 0.0)) for item in items) / len(items), 2
                ),
            }
        )
    next_state["conflicts"] = conflicts
    return next_state
