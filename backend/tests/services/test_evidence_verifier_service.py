from app.services.evidence_verifier_service import EvidenceVerifierService


def test_verifier_marks_issue_tool_verified_when_tool_succeeds():
    verifier = EvidenceVerifierService()

    result = verifier.verify(
        issue_id="iss_1",
        strategy="schema_diff",
        payload={"changed_files": ["backend/db/migrations/001.sql"]},
    )

    assert result["issue_id"] == "iss_1"
    assert result["tool_name"] == "schema_diff"
    assert result["tool_verified"] is True
    assert result["score"] >= 0.8
