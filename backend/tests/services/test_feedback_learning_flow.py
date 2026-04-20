from pathlib import Path

from app.domain.models.issue import DebateIssue
from app.repositories.fs import read_json

from app.services.review_service import ReviewService


def test_human_decision_persists_feedback_label(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/risk-guard",
            "target_ref": "main",
            "title": "security migration review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
        }
    )
    service.start_review(review.review_id)
    issue = next(item for item in service.list_issues(review.review_id) if item.needs_human)

    service.record_human_decision(review.review_id, issue.issue_id, "rejected", "误报")
    labels = service.list_feedback_labels(review.review_id)

    assert labels
    assert labels[0].label == "false_positive"


def test_human_decision_refreshes_report_summary_and_artifacts(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/high-risk-review",
            "target_ref": "main",
            "title": "security migration review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
        }
    )
    service.start_review(review.review_id)
    issue = next(item for item in service.list_issues(review.review_id) if item.needs_human)

    service.record_human_decision(
        review.review_id,
        issue.issue_id,
        "approved",
        "人工确认需要整改",
    )
    refreshed_review = service.get_review(review.review_id)
    artifact_dir = storage_root / "reviews" / review.review_id / "artifacts"
    summary_comment = read_json(artifact_dir / "summary_comment.json")
    check_run = read_json(artifact_dir / "check_run.json")

    assert refreshed_review is not None
    assert "0 个待人工裁决" in refreshed_review.report_summary
    assert summary_comment["human_review_status"] == "approved"
    assert "0 个待人工裁决" in summary_comment["summary"]
    assert check_run["status"] == "completed"
    assert check_run["conclusion"] == "completed"


def test_human_decision_can_continue_with_remaining_pending_issues(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/multi-human-gate",
            "target_ref": "main",
            "title": "multi pending human review",
            "changed_files": ["backend/app/security/authz.py"],
        }
    )
    pending_a = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_human_a",
        title="高风险权限问题 A",
        summary="需要人工确认 A。",
        file_path="backend/app/security/authz.py",
        line_start=12,
        status="needs_human",
        severity="high",
        confidence=0.9,
        finding_ids=["fdg_a"],
        participant_expert_ids=["security_compliance"],
        needs_human=True,
    )
    pending_b = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_human_b",
        title="高风险权限问题 B",
        summary="需要人工确认 B。",
        file_path="backend/app/security/authz.py",
        line_start=24,
        status="needs_human",
        severity="high",
        confidence=0.88,
        finding_ids=["fdg_b"],
        participant_expert_ids=["security_compliance"],
        needs_human=True,
    )
    service.issue_repo.save_all(review.review_id, [pending_a, pending_b])
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [pending_a.issue_id, pending_b.issue_id]
    service.review_repo.save(review)

    updated = service.record_human_decision(review.review_id, pending_a.issue_id, "approved", "先处理 A")

    assert updated.status == "waiting_human"
    assert updated.phase == "human_gate"
    assert updated.human_review_status == "requested"
    assert updated.pending_human_issue_ids == [pending_b.issue_id]

    refreshed_issues = service.list_issues(review.review_id)
    issue_a = next(item for item in refreshed_issues if item.issue_id == pending_a.issue_id)
    issue_b = next(item for item in refreshed_issues if item.issue_id == pending_b.issue_id)
    assert issue_a.status == "resolved"
    assert issue_b.status == "needs_human"
