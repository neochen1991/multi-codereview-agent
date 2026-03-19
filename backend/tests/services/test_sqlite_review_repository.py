from __future__ import annotations

from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.review import ReviewSubject, ReviewTask
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
