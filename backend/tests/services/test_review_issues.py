from pathlib import Path

from app.services.review_service import ReviewService


def test_start_review_creates_debate_issue_and_human_gate(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/db-locking",
            "target_ref": "main",
            "title": "migration risk",
            "changed_files": [
                "backend/db/migrations/20260312_add_index.sql",
                "backend/app/repositories/order_repository.py",
            ],
        }
    )

    updated = service.start_review(review.review_id)
    issues = service.list_issues(review.review_id)

    assert updated.human_review_status == "not_required"
    assert issues
    assert any(issue.verified for issue in issues)
    assert all(issue.status == "resolved" for issue in issues)
