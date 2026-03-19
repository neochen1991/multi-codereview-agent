def test_reviews_queue_returns_pending_items_only(client):
    first = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "mr_url": "https://github.com/example/repo/pull/1",
            "title": "first",
        },
    ).json()
    second = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "mr_url": "https://github.com/example/repo/pull/2",
            "title": "second",
        },
    ).json()
    completed = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "mr_url": "https://github.com/example/repo/pull/3",
            "title": "completed",
        },
    ).json()
    client.post(f"/api/reviews/{completed['review_id']}/start")

    response = client.get("/api/reviews/queue")
    assert response.status_code == 200
    queue = response.json()
    queue_ids = [item["review_id"] for item in queue]

    assert first["review_id"] in queue_ids
    assert second["review_id"] in queue_ids
    assert completed["review_id"] not in queue_ids
    assert queue_ids.index(first["review_id"]) < queue_ids.index(second["review_id"])


def test_reviews_queue_sync_reports_missing_repo_url_when_not_configured(client):
    client.put(
        "/api/settings/runtime",
        json={
            "auto_review_enabled": True,
            "auto_review_repo_url": "",
            "code_repo_clone_url": "",
        },
    )

    response = client.post("/api/reviews/queue/sync")
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["created_count"] == 0
    assert payload["started_review_id"] == ""
    assert payload["message"] == "未配置自动审核仓库地址"
