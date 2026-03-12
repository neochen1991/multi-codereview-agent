def test_create_review_returns_review_id(client):
    response = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "demo review",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["review_id"].startswith("rev_")
    assert payload["status"] == "pending"


def test_start_review_emits_review_started_event(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "branch",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "demo review",
        },
    ).json()

    response = client.post(f"/api/reviews/{created['review_id']}/start")
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] in {"running", "completed", "waiting_human"}

    review = client.get(f"/api/reviews/{created['review_id']}")
    assert review.status_code == 200
    review_payload = review.json()
    assert review_payload["started_at"] is not None


def test_list_reviews_includes_started_time_and_duration(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "branch",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "history review",
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    reviews = client.get("/api/reviews")
    assert reviews.status_code == 200
    payload = reviews.json()
    row = next(item for item in payload if item["review_id"] == created["review_id"])
    assert row["started_at"] is not None
    assert "duration_seconds" in row
