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
