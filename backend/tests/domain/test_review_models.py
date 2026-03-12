from app.domain.models.review import ReviewSubject, ReviewTask


def test_review_task_can_wrap_review_subject():
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_1",
        project_id="proj_1",
        source_ref="feature/demo",
        target_ref="main",
    )
    task = ReviewTask(review_id="rev_1", subject=subject, status="pending")
    assert task.subject.source_ref == "feature/demo"
    assert task.status == "pending"
