from __future__ import annotations

from app.services.evidence_verifier_service import EvidenceVerifierService
from app.services.orchestrator.state import ReviewState


def evidence_verification(state: ReviewState) -> ReviewState:
    next_state = dict(state)
    next_state["phase"] = "evidence_verification"
    verifier = EvidenceVerifierService()
    verified_issues: list[dict[str, object]] = []
    risk_hints = set(next_state.get("risk_hints", []))
    for issue in next_state.get("issues", []):
        strategy = _pick_verification_strategy(issue)
        topic = str(issue.get("topic", ""))
        verification_result = verifier.verify(
            issue_id=str(issue.get("issue_id", "")),
            strategy=strategy,
            payload={"changed_files": next_state.get("changed_files", [])},
        )
        verified = bool(verification_result.get("tool_verified"))
        confidence = float(issue.get("confidence", 0.0))
        if verified:
            confidence = min(0.98, round(max(confidence, float(verification_result.get("score", 0.0))), 2))
        needs_human = False
        severity = str(issue.get("severity", "medium"))
        if topic == "security" and "security_surface" in risk_hints:
            needs_human = True
        if topic == "database" and "database_migration" in risk_hints:
            needs_human = True
        if severity in {"blocker", "critical"}:
            needs_human = True
        next_issue = dict(issue)
        next_issue["verified"] = verified
        next_issue["confidence"] = confidence
        next_issue["needs_human"] = needs_human
        next_issue["verifier_name"] = "builtin_verifier"
        next_issue["tool_name"] = verification_result["tool_name"]
        next_issue["tool_verified"] = verification_result["tool_verified"]
        verified_issues.append(next_issue)
    next_state["issues"] = verified_issues
    return next_state


def _pick_verification_strategy(issue: dict[str, object]) -> str:
    finding_type = str(issue.get("finding_type") or "risk_hypothesis")
    file_path = str(issue.get("file_path") or "").lower()
    topic = str(issue.get("topic") or "").lower()
    participants = [str(item).lower() for item in list(issue.get("participant_expert_ids") or []) if str(item).strip()]
    evidence_blob = " ".join(str(item).lower() for item in issue.get("evidence", []))

    if finding_type == "test_gap" or any(token in f"{file_path} {topic} {evidence_blob}" for token in ["test", "spec", "jest", "vitest", "playwright"]):
        return "coverage_diff"

    if any(
        token in f"{file_path} {topic} {evidence_blob}"
        for token in ["migration", ".sql", "schema", "repository", "db"]
    ) or any(expert in {"database_analysis", "performance_reliability"} for expert in participants):
        return "schema_diff"

    return "local_diff"
