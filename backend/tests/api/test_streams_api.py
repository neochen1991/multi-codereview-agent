def test_events_endpoint_returns_existing_review_events(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "stream review",
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    response = client.get(f"/api/reviews/{created['review_id']}/events")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
