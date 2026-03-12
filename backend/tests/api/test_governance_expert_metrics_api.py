def test_governance_expert_metrics_endpoint_returns_expert_rows(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/expert-metrics",
            "target_ref": "main",
            "title": "expert metrics review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
        },
    ).json()
    client.post(f"/api/reviews/{created['review_id']}/start")
    issues = client.get(f"/api/reviews/{created['review_id']}/issues").json()
    target_issue = next(item for item in issues if item["needs_human"])
    client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": target_issue["issue_id"],
            "decision": "rejected",
            "comment": "误报",
        },
    )

    response = client.get("/api/governance/expert-metrics")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert payload
    assert "expert_id" in payload[0]
    assert "false_positive_count" in payload[0]
