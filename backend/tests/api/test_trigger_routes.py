def test_manual_branch_trigger_creates_review(client):
    response = client.post(
        "/api/triggers/manual",
        json={
            "subject_type": "branch",
            "repo_url": "https://git.example.com/platform/payments",
            "project_id": "platform",
            "repo_id": "payments",
            "source_ref": "feature/risk-guard",
            "target_ref": "main",
            "title": "manual trigger",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["review_id"].startswith("rev_")
    assert payload["status"] == "pending"
