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
        strategy = "local_diff"
        topic = str(issue.get("topic", ""))
        evidence_blob = " ".join(str(item).lower() for item in issue.get("evidence", []))
        if topic == "database" or any(token in evidence_blob for token in ["migration", ".sql", "schema"]):
            strategy = "schema_diff"
        elif topic == "test":
            strategy = "coverage_diff"
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
