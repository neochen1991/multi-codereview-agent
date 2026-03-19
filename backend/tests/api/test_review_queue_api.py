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
    first_row = next(item for item in queue if item["review_id"] == first["review_id"])
    second_row = next(item for item in queue if item["review_id"] == second["review_id"])
    assert first_row["queue_position"] == 1
    assert first_row["queue_blocker_code"] == "ready"
    assert second_row["queue_position"] == 2
    assert second_row["queue_blocker_code"] == "waiting_for_turn"
    assert "前方还有 1 条待处理任务" in second_row["queue_blocker_message"]


def test_reviews_queue_reports_running_review_blocker(client, monkeypatch):
    running = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_running",
            "project_id": "proj_running",
            "source_ref": "feature/running",
            "target_ref": "main",
            "title": "running review",
        },
    ).json()
    queued = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_queue",
            "project_id": "proj_queue",
            "source_ref": "feature/queue",
            "target_ref": "main",
            "title": "queued review",
        },
    ).json()

    def fake_start(review_id: str):
        review = client.app.state.auto_review_scheduler._review_service.get_review(review_id)  # type: ignore[attr-defined]
        assert review is not None
        review.status = "running"
        review.phase = "expert_review"
        client.app.state.auto_review_scheduler._review_service.review_repo.save(review)  # type: ignore[attr-defined]
        return review

    monkeypatch.setattr("app.api.routes.reviews.review_service_module.review_service.start_review_async", fake_start)

    client.post(f"/api/reviews/{running['review_id']}/start")

    response = client.get("/api/reviews/queue")
    assert response.status_code == 200
    queue = response.json()
    assert len(queue) == 1
    row = queue[0]
    assert row["review_id"] == queued["review_id"]
    assert row["queue_position"] == 1
    assert row["queue_blocker_code"] == "blocked_by_running_review"
    assert row["blocking_review_id"] == running["review_id"]
    assert running["review_id"] in row["queue_blocker_message"]


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


def test_reviews_queue_sync_prefers_code_repo_clone_url(client, monkeypatch):
    client.put(
        "/api/settings/runtime",
        json={
            "auto_review_enabled": True,
            "code_repo_clone_url": "codehub-g.huawei.com/PIP/FND/projectname/merge_requests",
            "auto_review_repo_url": "",
        },
    )

    captured: dict[str, str] = {}

    def fake_enqueue(repo_url: str):
        captured["repo_url"] = repo_url
        return []

    monkeypatch.setattr(
        "app.api.routes.reviews.review_service_module.review_service.enqueue_open_merge_requests",
        fake_enqueue,
    )
    monkeypatch.setattr(
        "app.api.routes.reviews.review_service_module.review_service.start_next_pending_review",
        lambda: None,
    )

    response = client.post("/api/reviews/queue/sync")
    assert response.status_code == 200
    payload = response.json()
    assert captured["repo_url"] == "codehub-g.huawei.com/PIP/FND/projectname/merge_requests"
    assert payload["repo_url"] == "codehub-g.huawei.com/PIP/FND/projectname/merge_requests"


def test_queue_start_review_prioritizes_pending_review_when_another_review_is_running(client, monkeypatch):
    running = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_running",
            "project_id": "proj_running",
            "source_ref": "feature/running",
            "target_ref": "main",
            "title": "running review",
        },
    ).json()
    queued = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_queue",
            "project_id": "proj_queue",
            "source_ref": "feature/queue",
            "target_ref": "main",
            "title": "queued review",
        },
    ).json()

    def fake_start(review_id: str):
        review = client.app.state.auto_review_scheduler._review_service.get_review(review_id)  # type: ignore[attr-defined]
        assert review is not None
        review.status = "running"
        review.phase = "expert_review"
        client.app.state.auto_review_scheduler._review_service.review_repo.save(review)  # type: ignore[attr-defined]
        return review

    monkeypatch.setattr("app.api.routes.reviews.review_service_module.review_service.start_review_async", fake_start)
    client.post(f"/api/reviews/{running['review_id']}/start")

    response = client.post(f"/api/reviews/{queued['review_id']}/queue-start")
    assert response.status_code == 202
    payload = response.json()
    assert payload["review_id"] == queued["review_id"]
    assert payload["status"] == "pending"
    assert "已插队" in payload["message"]

    queue = client.get("/api/reviews/queue").json()
    assert queue[0]["review_id"] == queued["review_id"]


def test_queue_start_review_immediately_starts_when_no_running_review(client, monkeypatch):
    queued = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_queue",
            "project_id": "proj_queue",
            "source_ref": "feature/queue",
            "target_ref": "main",
            "title": "queued review",
        },
    ).json()

    def fake_queue_start(review_id: str):
        review = client.app.state.auto_review_scheduler._review_service.get_review(review_id)  # type: ignore[attr-defined]
        assert review is not None
        review.status = "running"
        review.phase = "queued"
        client.app.state.auto_review_scheduler._review_service.review_repo.save(review)  # type: ignore[attr-defined]
        return review, "任务已立即启动。"

    monkeypatch.setattr("app.api.routes.reviews.review_service_module.review_service.queue_start_review", fake_queue_start)

    response = client.post(f"/api/reviews/{queued['review_id']}/queue-start")
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["phase"] == "queued"
    assert payload["message"] == "任务已立即启动。"
