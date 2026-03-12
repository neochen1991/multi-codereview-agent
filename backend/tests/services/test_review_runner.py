from pathlib import Path

from app.services.review_runner import ReviewRunner


def test_review_runner_emits_finding_created_event(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    runner.run_once(review_id)
    events = runner.list_events(review_id)
    assert any(event.event_type == "finding_created" for event in events)
