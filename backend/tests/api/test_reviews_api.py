import sqlite3
from pathlib import Path


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


def test_create_review_persists_design_docs_into_review_metadata(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "design review",
            "mr_url": "https://github.com/example/repo/pull/1",
            "design_docs": [
                {
                    "title": "订单创建详细设计",
                    "filename": "order-create-design.md",
                    "content": "# 订单创建详细设计\n\n## API\n- POST /api/orders",
                }
            ],
        },
    ).json()

    detail = client.get(f"/api/reviews/{created['review_id']}")
    assert detail.status_code == 200
    payload = detail.json()
    design_docs = payload["subject"]["metadata"]["design_docs"]
    assert len(design_docs) == 1
    assert design_docs[0]["doc_type"] == "design_spec"
    assert design_docs[0]["filename"] == "order-create-design.md"


def test_create_review_persists_into_sqlite_without_file_backed_review_json(client, storage_root: Path):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_sqlite",
            "project_id": "proj_sqlite",
            "source_ref": "feature/sqlite",
            "target_ref": "main",
            "title": "sqlite review",
        },
    ).json()

    review_id = created["review_id"]
    db_path = storage_root / "app.db"
    assert db_path.exists()

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT review_id, status FROM reviews WHERE review_id = ?",
            (review_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == review_id
    assert row[1] == "pending"

    assert not (storage_root / "reviews" / review_id / "review.json").exists()


def test_close_running_review_updates_status_to_closed(client, monkeypatch):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_close",
            "project_id": "proj_close",
            "source_ref": "feature/close",
            "target_ref": "main",
            "title": "close review",
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
    client.post(f"/api/reviews/{created['review_id']}/start")

    response = client.post(f"/api/reviews/{created['review_id']}/close")
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "closed"
    assert payload["phase"] == "closed"

    review = client.get(f"/api/reviews/{created['review_id']}").json()
    assert review["status"] == "closed"
    assert review["phase"] == "closed"
