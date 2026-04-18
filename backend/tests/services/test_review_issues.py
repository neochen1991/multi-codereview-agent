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
    assert all(issue.status in {"resolved", "needs_verification", "comment"} for issue in issues)
    assert all(issue.issue_id for issue in issues)


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


def test_list_issues_rehydrates_legacy_merged_issue_into_individual_findings(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_3",
            "project_id": "proj_3",
            "source_ref": "feature/legacy-merged-issue",
            "target_ref": "main",
            "title": "legacy merged issue",
        }
    )
    finding_a = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_split_a",
        expert_id="performance_reliability",
        title="循环内查库",
        summary="第 40 行在循环内部发起查询。",
        file_path="src/main/java/com/example/BatchJob.java",
        line_start=40,
        remediation_suggestion="把查询移出循环并做批量预取。",
    )
    finding_b = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_split_b",
        expert_id="correctness_business",
        title="异常被吞掉",
        summary="第 56 行 catch 后未处理异常。",
        file_path="src/main/java/com/example/BatchJob.java",
        line_start=56,
        remediation_suggestion="记录异常并返回受控失败。",
    )
    service.finding_repo.save(review.review_id, finding_a)
    service.finding_repo.save(review.review_id, finding_b)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_legacy_merged",
                title="同一代码行存在 2 个问题：循环内查库",
                summary="旧版合并 issue。",
                file_path="src/main/java/com/example/BatchJob.java",
                line_start=40,
                finding_ids=["fdg_split_a", "fdg_split_b"],
                aggregated_titles=["循环内查库", "异常被吞掉"],
            )
        ],
    )

    issues = service.list_issues(review.review_id)
    report = service.build_report(review.review_id)

    assert [item.issue_id for item in issues] == ["fdg_split_a", "fdg_split_b"]
    assert [item.title for item in issues] == ["循环内查库", "异常被吞掉"]
    assert [item.line_start for item in issues] == [40, 56]
    assert issues[0].finding_ids == ["fdg_split_a"]
    assert issues[1].finding_ids == ["fdg_split_b"]
    assert report.issue_count == 2
    assert [item.issue_id for item in report.issues] == ["fdg_split_a", "fdg_split_b"]
