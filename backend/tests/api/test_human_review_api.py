def test_human_review_decision_updates_issue_and_review(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/migration-guard",
            "target_ref": "main",
            "title": "security migration review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")

    issues_response = client.get(f"/api/reviews/{created['review_id']}/issues")
    assert issues_response.status_code == 200
    issues = issues_response.json()
    assert issues
    assert any(item["needs_human"] for item in issues)

    target_issue = next(item for item in issues if item["needs_human"])
    decision_response = client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": target_issue["issue_id"],
            "decision": "approved",
            "comment": "确认存在高风险，进入阻断整改。",
        },
    )

    assert decision_response.status_code == 202
    payload = decision_response.json()
    assert payload["review_id"] == created["review_id"]
    assert payload["human_review_status"] == "approved"

    detail_response = client.get(f"/api/reviews/{created['review_id']}")
    detail_payload = detail_response.json()
    assert detail_payload["human_review_status"] == "approved"
    assert target_issue["issue_id"] not in detail_payload["pending_human_issue_ids"]

    messages_response = client.get(
        f"/api/reviews/{created['review_id']}/issues/{target_issue['issue_id']}/messages"
    )
    messages = messages_response.json()
    assert any(item["message_type"] == "human_comment" for item in messages)


def test_human_review_decision_rejects_repeat_submit_on_resolved_issue(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/migration-guard",
            "target_ref": "main",
            "title": "security migration review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    issues = client.get(f"/api/reviews/{created['review_id']}/issues").json()
    target_issue = next(item for item in issues if item["needs_human"])

    first_submit = client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": target_issue["issue_id"],
            "decision": "approved",
            "comment": "第一次裁决",
        },
    )
    assert first_submit.status_code == 202

    second_submit = client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": target_issue["issue_id"],
            "decision": "approved",
            "comment": "重复提交",
        },
    )
    assert second_submit.status_code == 409
    assert second_submit.json()["detail"] == "issue is not pending human decision"
