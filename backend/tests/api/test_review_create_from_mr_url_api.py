def test_review_create_accepts_mr_url_without_explicit_repo_fields(client):
    response = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "mr_url": "https://git.example.com/platform/payments/-/merge_requests/128",
            "title": "MR url review",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["review_id"].startswith("rev_")


def test_review_create_persists_selected_experts(client):
    response = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "mr_url": "https://github.com/example/repo/commit/abcdef1234567890",
            "selected_experts": ["correctness_business", "database_analysis", "mq_analysis"],
        },
    )

    assert response.status_code == 201
    review_id = response.json()["review_id"]

    detail = client.get(f"/api/reviews/{review_id}")
    assert detail.status_code == 200
    assert detail.json()["selected_experts"] == [
        "correctness_business",
        "database_analysis",
        "mq_analysis",
    ]
