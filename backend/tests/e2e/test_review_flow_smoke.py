def test_review_flow_smoke(client):
    response = client.post(
        "/api/reviews",
        json={
            "subject_type": "branch",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "smoke review",
        },
    )
    assert response.status_code == 201
