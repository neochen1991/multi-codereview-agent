from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage


def _seed_replay_human_issue(review_id: str) -> None:
    import app.services.review_service as review_service_module

    service = review_service_module.review_service
    finding = ReviewFinding(
        review_id=review_id,
        finding_id="fdg_replay_seed",
        expert_id="security_compliance",
        title="权限失败路径未处理",
        summary="权限失败路径未处理。",
        file_path="backend/app/security/authz.py",
        line_start=12,
        suggested_code="raise PermissionDenied()",
    )
    issue = DebateIssue(
        review_id=review_id,
        issue_id="iss_replay_seed",
        title=finding.title,
        summary=finding.summary,
        file_path=finding.file_path,
        line_start=finding.line_start,
        status="needs_human",
        severity="high",
        confidence=0.91,
        finding_ids=[finding.finding_id],
        participant_expert_ids=[finding.expert_id],
        needs_human=True,
    )
    service.finding_repo.save_many(review_id, [finding])
    service.issue_repo.save_all(review_id, [issue])
    review = service.get_review(review_id)
    assert review is not None
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [issue.issue_id]
    service.review_repo.save(review)


def test_replay_endpoint_returns_full_review_bundle(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
            "title": "replay review",
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    response = client.get(f"/api/reviews/{created['review_id']}/replay")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review"]["review_id"] == created["review_id"]
    assert isinstance(payload["events"], list)
    assert isinstance(payload["issues"], list)
    assert isinstance(payload["messages"], list)
    assert "report" in payload


def test_replay_endpoint_returns_refreshed_summary_after_human_decision(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/security-e2e",
            "target_ref": "main",
            "title": "replay refresh review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    _seed_replay_human_issue(created["review_id"])
    issues = client.get(f"/api/reviews/{created['review_id']}/issues").json()
    target_issue = next(item for item in issues if item["needs_human"])
    client.post(
        f"/api/reviews/{created['review_id']}/human-decisions",
        json={
            "issue_id": target_issue["issue_id"],
            "decision": "approved",
            "comment": "accept",
        },
    )
    response = client.get(f"/api/reviews/{created['review_id']}/replay")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review"]["status"] == "completed"
    assert "0 个待人工裁决" in payload["review"]["report_summary"]
    assert payload["report"]["status"] == "completed"
    assert payload["feedback_labels"]


def test_replay_endpoint_exposes_analysis_content_and_candidate_verification(client):
    import app.services.review_service as review_service_module

    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/replay-diagnostics",
            "target_ref": "main",
            "title": "replay diagnostics",
        },
    ).json()
    service = review_service_module.review_service
    service.message_repo.append(
        ConversationMessage(
            review_id=created["review_id"],
            issue_id="fdg_diag",
            expert_id="ddd_architecture",
            message_type="expert_analysis",
            content='{"rule_check_results":[{"rule_id":"ARCH-JDDD-002","status":"violated"}],"candidate_findings":[]}',
            metadata={
                "file_path": "src/CourseCreator.java",
                "rule_screening": {"matched_rules_for_llm": [{"rule_id": "ARCH-JDDD-002"}]},
                "candidate_verification": {"status": "accepted", "rule_id": "ARCH-JDDD-002"},
                "prompt_snapshot_summary": {
                    "profile": "rule-guided-compact",
                    "contains_rule_cards": True,
                    "contains_context_packet": True,
                },
                "prompt_snapshot_full": "[RULE_CARDS]\nARCH-JDDD-002\n[CONTEXT_PACKET]\nCourseCreator",
                "model_raw_response_excerpt": '{"rule_check_results":[{"rule_id":"ARCH-JDDD-002"}]}',
                "model_raw_response_full": '{"rule_check_results":[{"rule_id":"ARCH-JDDD-002","status":"violated"}],"candidate_findings":[]}',
                "rule_check_results": [{"rule_id": "ARCH-JDDD-002", "status": "violated"}],
                "candidate_findings": [{"rule_id": "ARCH-JDDD-002", "title": "factory bypass"}],
                "rule_coverage": {"matched_rule_count": 1, "checked_rule_count": 1, "candidate_count": 1},
                "context_gaps": [],
            },
        )
    )

    payload = client.get(f"/api/reviews/{created['review_id']}/replay").json()
    diagnostic = next(item for item in payload["messages"] if item["issue_id"] == "fdg_diag")

    assert "rule_check_results" in diagnostic["content"]
    assert diagnostic["metadata"]["candidate_verification"]["status"] == "accepted"
    assert diagnostic["metadata"]["prompt_snapshot_summary"]["contains_rule_cards"] is True
    assert "ARCH-JDDD-002" in diagnostic["metadata"]["prompt_snapshot_full"]
    assert "candidate_findings" in diagnostic["metadata"]["model_raw_response_full"]
    assert diagnostic["metadata"]["rule_check_results"][0]["status"] == "violated"
    assert diagnostic["metadata"]["candidate_findings"][0]["title"] == "factory bypass"
    assert diagnostic["metadata"]["rule_coverage"]["checked_rule_count"] == 1
