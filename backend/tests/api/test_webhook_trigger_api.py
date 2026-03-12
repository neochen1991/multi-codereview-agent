def test_webhook_trigger_creates_review_from_platform_payload(client):
    response = client.post(
        "/api/triggers/webhook",
        json={
            "provider": "gitlab",
            "event_type": "merge_request",
            "repository": {"project_id": "platform", "repo_id": "payments"},
            "merge_request": {
                "iid": 42,
                "title": "Webhook review",
                "source_branch": "feature/webhook",
                "target_branch": "main",
                "url": "https://git.example.com/platform/payments/-/merge_requests/42",
            },
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["review_id"].startswith("rev_")
