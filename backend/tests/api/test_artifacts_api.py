def test_artifacts_endpoint_returns_published_review_artifacts(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/artifacts",
            "target_ref": "main",
            "title": "artifact review",
        },
    ).json()
    client.post(f"/api/reviews/{created['review_id']}/start")

    response = client.get(f"/api/reviews/{created['review_id']}/artifacts")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary_comment"]["review_id"] == created["review_id"]
    assert payload["check_run"]["name"] == "multi-agent-code-review"
