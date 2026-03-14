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


def test_upload_same_knowledge_doc_does_not_create_duplicates(client):
    payload = {
        "title": "Schema diff checklist",
        "expert_id": "performance_reliability",
        "content": "涉及 migration 时必须检查锁与回滚。",
        "tags": ["migration", "schema"],
        "source_filename": "schema-checklist.md",
    }

    first = client.post("/api/knowledge/upload", json=payload)
    second = client.post("/api/knowledge/upload", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201

    grouped = client.get("/api/knowledge/grouped")
    assert grouped.status_code == 200
    docs = grouped.json()["performance_reliability"]
    assert len([item for item in docs if item["title"] == "Schema diff checklist"]) == 1


def test_upload_same_identity_knowledge_doc_replaces_old_content(client):
    first = client.post(
        "/api/knowledge/upload",
        json={
            "title": "security_compliance 长版审视规范",
            "expert_id": "security_compliance",
            "doc_type": "review_rule",
            "content": "旧版本内容",
            "tags": ["security"],
            "source_filename": "security_compliance.md",
        },
    )
    second = client.post(
        "/api/knowledge/upload",
        json={
            "title": "security_compliance 长版审视规范",
            "expert_id": "security_compliance",
            "doc_type": "review_rule",
            "content": "新版本内容",
            "tags": ["security", "owasp"],
            "source_filename": "security_compliance.md",
        },
    )

    assert first.status_code == 201
    assert second.status_code == 201

    grouped = client.get("/api/knowledge/grouped")
    assert grouped.status_code == 200
    docs = [
        item
        for item in grouped.json()["security_compliance"]
        if item["title"] == "security_compliance 长版审视规范"
    ]
    assert len(docs) == 1
    assert docs[0]["content"] == "新版本内容"


def test_delete_knowledge_doc_unbinds_and_removes_document(client):
    created = client.post(
        "/api/knowledge/upload",
        json={
            "title": "Redis 排查手册",
            "expert_id": "redis_analysis",
            "doc_type": "runbook",
            "content": "先检查 key、TTL 和热点。",
            "tags": ["redis", "runbook"],
            "source_filename": "redis-runbook.md",
        },
    )
    assert created.status_code == 201
    doc_id = created.json()["doc_id"]

    deleted = client.delete(f"/api/knowledge/{doc_id}")
    assert deleted.status_code == 204

    grouped = client.get("/api/knowledge/grouped")
    assert grouped.status_code == 200
    docs = grouped.json().get("redis_analysis", [])
    assert all(item["doc_id"] != doc_id for item in docs)
