from pathlib import Path

from app.services.review_runner import ReviewRunner


def test_review_runner_publishes_report_artifacts(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()

    runner.run_once(review_id)

    artifact_dir = storage_root / "reviews" / review_id / "artifacts"
    assert (artifact_dir / "summary_comment.json").exists()
    assert (artifact_dir / "check_run.json").exists()
