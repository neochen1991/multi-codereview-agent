from pathlib import Path

from app.domain.models.review import ReviewSubject, ReviewTask
from app.repositories.file_review_repository import FileReviewRepository


def test_file_review_repository_saves_and_loads_review(storage_root: Path):
    repo = FileReviewRepository(storage_root)
    task = ReviewTask(
        review_id="rev_1",
        status="pending",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo_1",
            project_id="proj_1",
            source_ref="feature/demo",
            target_ref="main",
        ),
    )

    repo.save(task)
    loaded = repo.get("rev_1")

    assert loaded is not None
    assert loaded.review_id == "rev_1"
    assert loaded.subject.target_ref == "main"
