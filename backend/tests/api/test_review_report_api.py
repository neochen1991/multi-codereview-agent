def test_review_report_endpoint_returns_structured_payload(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "branch",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "report review",
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    response = client.get(f"/api/reviews/{created['review_id']}/report")
    assert response.status_code == 200
    payload = response.json()
    assert payload["review_id"] == created["review_id"]
    assert isinstance(payload["findings"], list)
    assert isinstance(payload["issues"], list)
    assert "confidence_summary" in payload
    assert "llm_usage_summary" in payload
    assert "human_review_status" in payload
    assert "summary" in payload
    assert payload["findings"][0]["remediation_strategy"]
    assert payload["findings"][0]["remediation_suggestion"]
    assert payload["findings"][0]["remediation_steps"]
    assert payload["findings"][0]["code_excerpt"]
    assert payload["findings"][0]["suggested_code"]
    assert payload["findings"][0]["file_path"] in payload["findings"][0]["code_excerpt"]
