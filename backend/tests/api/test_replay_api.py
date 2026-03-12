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
