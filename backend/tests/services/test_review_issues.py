from pathlib import Path

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
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
    assert all(issue.tool_name for issue in issues)
    assert all(issue.status in {"resolved", "needs_verification", "comment"} for issue in issues)


def test_list_issues_realigns_issue_location_from_linked_finding(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_2",
            "project_id": "proj_2",
            "source_ref": "feature/location-fix",
            "target_ref": "main",
            "title": "issue location fix",
        }
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_location_1",
        expert_id="correctness_business",
        title="真实问题行号",
        summary="问题实际发生在 88 行。",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=88,
    )
    service.finding_repo.save(review.review_id, finding)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_location_1",
                title="错误 issue 行号",
                summary="issue 持久化里带了错误行号。",
                file_path="src/main/java/com/example/Wrong.java",
                line_start=3,
                finding_ids=["fdg_location_1"],
            )
        ],
    )

    issues = service.list_issues(review.review_id)
    report = service.build_report(review.review_id)

    assert issues[0].file_path == "src/main/java/com/example/OrderService.java"
    assert issues[0].line_start == 88
    assert report.issues[0].file_path == "src/main/java/com/example/OrderService.java"
    assert report.issues[0].line_start == 88
