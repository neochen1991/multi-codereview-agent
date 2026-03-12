def test_create_knowledge_doc(client):
    response = client.post(
        "/api/knowledge/docs",
        json={
            "title": "Schema diff checklist",
            "expert_id": "performance_reliability",
            "content": "涉及 migration 时必须检查锁与回滚。",
            "tags": ["migration", "schema"],
            "source_filename": "schema-checklist.md",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["title"] == "Schema diff checklist"
    assert payload["expert_id"] == "performance_reliability"
    assert payload["source_filename"] == "schema-checklist.md"


def test_upload_knowledge_doc_and_group_by_expert(client):
    upload = client.post(
        "/api/knowledge/upload",
        json={
            "title": "Auth guideline",
            "expert_id": "security_compliance",
            "content": "# auth\n权限校验必须覆盖拒绝路径。",
            "tags": ["auth"],
            "source_filename": "auth-guideline.md",
        },
    )
    assert upload.status_code == 201

    grouped = client.get("/api/knowledge/grouped")
    assert grouped.status_code == 200
    payload = grouped.json()
    assert "security_compliance" in payload
    assert any(item["source_filename"] == "auth-guideline.md" for item in payload["security_compliance"])
