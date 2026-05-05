from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue


def _seed_replay_human_issue(review_id: str) -> None:
    import app.services.review_service as review_service_module

    service = review_service_module.review_service
    finding = ReviewFinding(
        review_id=review_id,
        finding_id="fdg_replay_seed",
        expert_id="security_compliance",
        title="权限失败路径未处理",
        summary="权限失败路径未处理。",
        file_path="backend/app/security/authz.py",
        line_start=12,
        suggested_code="raise PermissionDenied()",
    )
    issue = DebateIssue(
        review_id=review_id,
        issue_id="iss_replay_seed",
        title=finding.title,
        summary=finding.summary,
        file_path=finding.file_path,
        line_start=finding.line_start,
        status="needs_human",
        severity="high",
        confidence=0.91,
        finding_ids=[finding.finding_id],
        participant_expert_ids=[finding.expert_id],
        needs_human=True,
    )
    service.finding_repo.save_many(review_id, [finding])
    service.issue_repo.save_all(review_id, [issue])
    review = service.get_review(review_id)
    assert review is not None
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [issue.issue_id]
    service.review_repo.save(review)


def test_replay_endpoint_returns_full_review_bundle(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "replay review",
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    response = client.get(f"/api/reviews/{created['review_id']}/replay")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review"]["review_id"] == created["review_id"]
    assert isinstance(payload["events"], list)
    assert isinstance(payload["issues"], list)
    assert isinstance(payload["messages"], list)
    assert "report" in payload


def test_replay_endpoint_returns_refreshed_summary_after_human_decision(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/security-e2e",
            "target_ref": "main",
            "title": "replay refresh review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    _seed_replay_human_issue(created["review_id"])
    issues = client.get(f"/api/reviews/{created['review_id']}/issues").json()
    target_issue = next(item for item in issues if item["needs_human"])
    client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": target_issue["issue_id"],
            "decision": "approved",
            "comment": "accept",
        },
    )
    response = client.get(f"/api/reviews/{created['review_id']}/replay")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review"]["status"] == "completed"
    assert "0 个待人工裁决" in payload["review"]["report_summary"]
    assert payload["report"]["status"] == "completed"
    assert payload["feedback_labels"]
