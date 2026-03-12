from pathlib import Path

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
