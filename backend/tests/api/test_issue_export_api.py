from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue


def _seed_export_issue(review_id: str) -> None:
    import app.services.review_service as review_service_module

    service = review_service_module.review_service
    finding = ReviewFinding(
        review_id=review_id,
        finding_id="fdg_export_seed",
        expert_id="correctness_business",
        title="订单创建失败路径未处理",
        summary="订单创建失败路径未处理。",
        file_path="backend/app/orders/service.py",
        line_start=12,
        suggested_code="if (!repository.save(order)) { throw new OrderCreateException(\"create failed\"); }",
    )
    issue = DebateIssue(
        review_id=review_id,
        issue_id="iss_export_seed",
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
        remediation_suggestion="补齐保存失败的错误处理，并用测试覆盖失败分支。",
        suggested_code=finding.suggested_code,
    )
    service.finding_repo.save_many(review_id, [finding])
    service.issue_repo.save_all(review_id, [issue])


def test_export_issues_to_codehub_mock_returns_selected_issue_payloads(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_export",
            "project_id": "proj_export",
            "source_ref": "feature/export-codehub",
            "target_ref": "main",
            "title": "export issues to codehub",
            "changed_files": [
                "backend/app/security/authz.py",
                "backend/app/orders/service.py",
            ],
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    _seed_export_issue(created["review_id"])

    issues_response = client.get(f"/api/reviews/{created['review_id']}/issues")
    findings_response = client.get(f"/api/reviews/{created['review_id']}/findings")
    assert issues_response.status_code == 200
    assert findings_response.status_code == 200

    issues = issues_response.json()
    findings = findings_response.json()
    assert issues
    assert findings

    target_issue = issues[0]
    response = client.post(
        f"/api/reviews/{created['review_id']}/issues/export/codehub",
        json={"issue_ids": [target_issue["issue_id"]]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_id"] == created["review_id"]
    assert payload["submitted_count"] == 1
    assert payload["status"] == "mock_submitted"
    assert len(payload["items"]) == 1

    exported = payload["items"][0]
    assert exported["issue_id"] == target_issue["issue_id"]
    assert exported["title"] == target_issue["title"]
    assert exported["problem_description"]
    assert exported["remediation_suggestion"]
    assert "mock://codehub/issues/" in exported["mock_ticket_url"]

    related_finding_ids = set(target_issue["finding_ids"])
    related_findings = [item for item in findings if item["finding_id"] in related_finding_ids]
    assert exported["patched_code"]
    assert any(item.get("suggested_code") == exported["patched_code"] for item in related_findings)
