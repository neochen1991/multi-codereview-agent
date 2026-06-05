from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewSubject, ReviewTask
from app.repositories.sqlite_issue_repository import SqliteIssueRepository
from app.repositories.sqlite_review_repository import SqliteReviewRepository


def test_sqlite_review_repository_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteReviewRepository(db_path)
    review = ReviewTask(
        review_id="rev_demo001",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="project",
            source_ref="feature/demo",
            target_ref="main",
            title="demo review",
            changed_files=["src/demo/App.java"],
            unified_diff="@@ -1 +1 @@",
        ),
        status="pending",
        phase="pending",
        analysis_mode="light",
        selected_experts=["correctness_business", "architecture_design"],
    )

    repository.save(review)

    loaded = repository.get(review.review_id)

    assert loaded is not None
    assert loaded.review_id == review.review_id
    assert loaded.subject.title == "demo review"
    assert loaded.selected_experts == ["correctness_business", "architecture_design"]
    assert repository.list()[0].review_id == review.review_id


def test_sqlite_review_light_issue_count_matches_effective_issue_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    review_repository = SqliteReviewRepository(db_path)
    issue_repository = SqliteIssueRepository(db_path)
    review = ReviewTask(
        review_id="rev_effective_count",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="project",
            source_ref="feature/demo",
            target_ref="main",
            title="demo review",
            changed_files=["src/demo/App.java"],
            unified_diff="@@ -1 +1 @@",
        ),
        status="completed",
        phase="completed",
        analysis_mode="standard",
        selected_experts=["correctness_business"],
    )

    review_repository.save(review)
    issue_repository.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_formal",
                title="正式问题",
                summary="这条问题应进入有效问题清单。",
                status="open",
                resolution="",
                severity="high",
                confidence=0.9,
                evidence_chain=[{"step": "code", "summary": "证据"}],
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_needs_verification",
                title="待验证问题",
                summary="这条问题只保留观察。",
                status="needs_verification",
                resolution="llm_judge_needs_verification",
                severity="medium",
                confidence=0.7,
                evidence_chain=[{"step": "code", "summary": "证据"}],
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_rejected",
                title="人工拒绝问题",
                summary="这条问题被人工拒绝。",
                status="resolved",
                resolution="human_rejected",
                human_decision="rejected",
                severity="medium",
                confidence=0.8,
            ),
        ],
    )

    rows = review_repository.list_light()

    assert rows[0]["review_id"] == review.review_id
    assert rows[0]["issue_count"] == 1
    assert rows[0]["quality_summary"]["evidence_chain_issue_count"] == 1


def test_sqlite_review_light_filters_project_and_limits_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteReviewRepository(db_path)
    base_time = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)
    for index, project_id in enumerate(["project_a", "project_b", "project_a"]):
        review = ReviewTask(
            review_id=f"rev_project_{index}",
            subject=ReviewSubject(
                subject_type="mr",
                repo_id="repo",
                project_id=project_id,
                source_ref=f"feature/{index}",
                target_ref="main",
                title=f"review {index}",
            ),
            status="completed",
            phase="completed",
            analysis_mode="standard",
            selected_experts=["correctness_business"],
            created_at=base_time + timedelta(minutes=index),
            updated_at=base_time + timedelta(minutes=index),
        )
        repository.save(review)

    rows = repository.list_light(project_id="project_a", limit=1)

    assert [item["review_id"] for item in rows] == ["rev_project_2"]


def test_sqlite_review_light_can_skip_counts_for_queue_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteReviewRepository(db_path)
    for status in ["pending", "running", "completed"]:
        repository.save(
            ReviewTask(
                review_id=f"rev_{status}",
                subject=ReviewSubject(
                    subject_type="mr",
                    repo_id="repo",
                    project_id="project",
                    source_ref=f"feature/{status}",
                    target_ref="main",
                    title=f"{status} review",
                ),
                status=status,
                phase=status,
                analysis_mode="standard",
                selected_experts=["correctness_business"],
            )
        )

    rows = repository.list_light(statuses=["pending", "running"], include_counts=False)

    assert {item["status"] for item in rows} == {"pending", "running"}
    assert all(item["issue_count"] == 0 and item["finding_count"] == 0 for item in rows)
