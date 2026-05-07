from app.domain.models.issue import DebateIssue


def test_governance_endpoint_returns_quality_metrics(client):
    response = client.get("/api/governance/quality-metrics")

    assert response.status_code == 200
    payload = response.json()
    assert "tool_confirmation_rate" in payload
    assert "debate_survival_rate" in payload


def test_governance_endpoint_returns_llm_timeout_metrics(client):
    response = client.get("/api/governance/llm-timeout-metrics")

    assert response.status_code == 200
    payload = response.json()
    assert "timeout_count" in payload
    assert "recent_timeouts" in payload


def test_governance_endpoint_returns_runtime_threshold_recommendations(client):
    response = client.get("/api/governance/runtime-threshold-recommendations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["applied"] is False
    assert "recommended_thresholds" in payload
    assert "issue_confidence_threshold_p2" in payload["recommended_thresholds"]


def test_governance_endpoint_returns_review_learning_cases(client):
    response = client.get("/api/governance/review-learning-cases")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)


def test_governance_review_learning_cases_support_repo_and_issue_type_filters(client):
    import app.services.review_service as review_service_module

    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_learning_filter",
            "project_id": "proj_java",
            "source_ref": "feature/comment-contract",
            "target_ref": "dev",
            "title": "learning filter review",
            "changed_files": ["src/main/java/com/acme/order/OrderRepository.java"],
        },
    ).json()
    review_id = str(created["review_id"])
    issue = DebateIssue(
        review_id=review_id,
        issue_id="iss_learning_filter",
        title="接口声明的方法未在实现类落地",
        summary="OrderRepository 接口定义了 findActive 方法，但实现类没有实际实现。",
        file_path="src/main/java/com/acme/order/OrderRepository.java",
        line_start=18,
        status="needs_human",
        normalized_issue_type="comment_contract_unimplemented",
        evidence=[
            "JdbcOrderRepository implements OrderRepository。",
            "JdbcOrderRepository 中存在 @Override public List<Order> findActive() { return jdbc.query(...); }。",
        ],
        context_files=["src/main/java/com/acme/order/JdbcOrderRepository.java"],
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

    client.post(
        f"/api/reviews/{review_id}/human-decisions",
        json={
            "issue_id": issue.issue_id,
            "decision": "rejected",
            "comment": "误报：实现类已 implements 接口，并且 @Override 了同名方法。",
        },
    )

    matched = client.get(
        "/api/governance/review-learning-cases",
        params={"repo_id": "repo_learning_filter", "issue_type": "comment_contract_unimplemented"},
    ).json()
    unmatched = client.get(
        "/api/governance/review-learning-cases",
        params={"repo_id": "repo_learning_filter", "issue_type": "missing_auth_check"},
    ).json()

    assert [item["issue_id"] for item in matched] == [issue.issue_id]
    assert unmatched == []


def test_governance_can_disable_and_enable_review_learning_case(client):
    import app.services.review_service as review_service_module

    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_learning_toggle",
            "project_id": "proj_java",
            "source_ref": "feature/comment-contract",
            "target_ref": "dev",
            "title": "learning toggle review",
            "changed_files": ["src/main/java/com/acme/order/OrderRepository.java"],
        },
    ).json()
    review_id = str(created["review_id"])
    issue = DebateIssue(
        review_id=review_id,
        issue_id="iss_learning_toggle",
        title="接口声明的方法未在实现类落地",
        summary="OrderRepository 接口定义了 findActive 方法，但实现类没有实际实现。",
        file_path="src/main/java/com/acme/order/OrderRepository.java",
        line_start=18,
        status="needs_human",
        normalized_issue_type="comment_contract_unimplemented",
        evidence=[
            "JdbcOrderRepository implements OrderRepository。",
            "JdbcOrderRepository 中存在 @Override public List<Order> findActive() { return jdbc.query(...); }。",
        ],
        context_files=["src/main/java/com/acme/order/JdbcOrderRepository.java"],
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

    client.post(
        f"/api/reviews/{review_id}/human-decisions",
        json={
            "issue_id": issue.issue_id,
            "decision": "rejected",
            "comment": "误报：实现类已 implements 接口，并且 @Override 了同名方法。",
        },
    )
    case = client.get("/api/governance/review-learning-cases", params={"repo_id": "repo_learning_toggle"}).json()[0]

    disabled = client.patch(
        f"/api/governance/review-learning-cases/{case['case_id']}",
        json={"status": "disabled"},
    )
    enabled = client.patch(
        f"/api/governance/review-learning-cases/{case['case_id']}",
        json={"status": "active"},
    )

    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"
