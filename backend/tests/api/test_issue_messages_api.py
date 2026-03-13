def test_issue_messages_endpoint_returns_thread_messages(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "branch",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "issue review",
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    findings = client.get(f"/api/reviews/{created['review_id']}/findings").json()
    assert findings

    issue_id = findings[0]["finding_id"]
    response = client.get(f"/api/reviews/{created['review_id']}/issues/{issue_id}/messages")
    assert response.status_code == 200
    messages = response.json()
    assert isinstance(messages, list)
    assert messages
    assert messages[0]["issue_id"] == issue_id
