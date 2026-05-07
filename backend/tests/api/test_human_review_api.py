from app.domain.models.issue import DebateIssue


def _create_review_with_pending_human_issue(client) -> tuple[dict[str, object], DebateIssue]:
    import app.services.review_service as review_service_module

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
    review_id = str(created["review_id"])
    issue = DebateIssue(
        review_id=review_id,
        issue_id="iss_pending_human",
        title="高风险权限问题",
        summary="需要人工确认是否阻断合入。",
        file_path="backend/app/security/authz.py",
        line_start=42,
        status="needs_human",
        severity="high",
        confidence=0.91,
        finding_ids=["fdg_authz"],
        participant_expert_ids=["security_compliance"],
        needs_human=True,
    )
    service = review_service_module.review_service
    service.issue_repo.save_all(review_id, [issue])
    review = service.get_review(review_id)
    assert review is not None
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [issue.issue_id]
    service.review_repo.save(review)
    return created, issue


def test_human_review_decision_updates_issue_and_review(client):
    created, seeded_issue = _create_review_with_pending_human_issue(client)

    issues_response = client.get(f"/api/reviews/{created['review_id']}/issues")
    assert issues_response.status_code == 200
    issues = issues_response.json()
    assert issues
    assert any(item["needs_human"] for item in issues)

    decision_response = client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": seeded_issue.issue_id,
            "decision": "approved",
            "comment": "确认存在高风险，进入阻断整改。",
        },
    )

    assert decision_response.status_code == 202
    payload = decision_response.json()
    assert payload["review_id"] == created["review_id"]
    assert payload["human_review_status"] == "approved"
    assert payload["learning_recorded"] is True
    assert payload["learning_effect"] == "confirmed_sample"

    detail_response = client.get(f"/api/reviews/{created['review_id']}")
    detail_payload = detail_response.json()
    assert detail_payload["human_review_status"] == "approved"
    assert seeded_issue.issue_id not in detail_payload["pending_human_issue_ids"]

    messages_response = client.get(
        f"/api/reviews/{created['review_id']}/issues/{seeded_issue.issue_id}/messages"
    )
    messages = messages_response.json()
    assert any(item["message_type"] == "human_comment" for item in messages)


def test_human_rejected_issue_is_removed_from_formal_issue_lists(client):
    created, seeded_issue = _create_review_with_pending_human_issue(client)

    decision_response = client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": seeded_issue.issue_id,
            "decision": "rejected",
            "comment": "人工确认这是误报，不进入正式修复议题。",
        },
    )
    assert decision_response.status_code == 202
    assert decision_response.json()["learning_effect"] == "false_positive_sample"

    formal_issues = client.get(f"/api/reviews/{created['review_id']}/issues").json()
    report = client.get(f"/api/reviews/{created['review_id']}/report").json()

    assert seeded_issue.issue_id not in {item["issue_id"] for item in formal_issues}
    assert seeded_issue.issue_id not in {item["issue_id"] for item in report["issues"]}


def test_human_review_decision_rejects_repeat_submit_on_resolved_issue(client):
    created, seeded_issue = _create_review_with_pending_human_issue(client)

    first_submit = client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": seeded_issue.issue_id,
            "decision": "approved",
            "comment": "第一次裁决",
        },
    )
    assert first_submit.status_code == 202

    second_submit = client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": seeded_issue.issue_id,
            "decision": "approved",
            "comment": "重复提交",
        },
    )
    assert second_submit.status_code == 409
    assert second_submit.json()["detail"] == "issue is not pending human decision"


def test_human_review_issues_include_canonical_issue_id(client):
    created, seeded_issue = _create_review_with_pending_human_issue(client)

    issues = client.get(f"/api/reviews/{created['review_id']}/issues").json()
    target_issue = next(item for item in issues if item["issue_id"] == seeded_issue.issue_id)

    assert target_issue["canonical_issue_id"] == target_issue["issue_id"]


def test_impact_feedback_api_records_path_decision(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/impact-feedback-api",
            "target_ref": "main",
            "title": "impact feedback api review",
            "changed_files": ["backend/app/orders/service.py"],
        },
    ).json()

    response = client.post(
        f"/api/reviews/{created['review_id']}/impact-feedback",
        json={
            "target_type": "impact_path",
            "target_key": "OrderController -> OrderService -> PaymentClient",
            "label": "confirmed",
            "comment": "实际验证命中",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["review_id"] == created["review_id"]
    assert payload["issue_id"].startswith("impact:impact_path:")
    assert payload["label"] == "impact_confirmed"
    assert payload["source"] == "impact_feedback"


def test_governance_impact_feedback_profiles_api_includes_recorded_feedback(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/impact-feedback-governance",
            "target_ref": "main",
            "title": "impact feedback governance review",
            "changed_files": ["backend/app/orders/service.py"],
        },
    ).json()
    client.post(
        f"/api/reviews/{created['review_id']}/impact-feedback",
        json={
            "target_type": "impact_path",
            "target_key": "OrderController -> OrderService -> PaymentClient",
            "label": "false_positive",
            "comment": "验证未命中",
        },
    )

    response = client.get("/api/governance/impact-feedback-profiles")

    assert response.status_code == 200
    payload = response.json()
    target_profile = payload["targets"]["impact_path:OrderController -> OrderService -> PaymentClient"]
    assert payload["summary"]["sample_count"] == 1
    assert target_profile["false_positive_count"] == 1
    assert target_profile["recommended_action"] == "watch_for_false_positive"
