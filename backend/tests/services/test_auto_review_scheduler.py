from app.services.auto_review_scheduler import AutoReviewScheduler


class _FakeReviewService:
    def __init__(self) -> None:
        self.recovered = False

    def recover_interrupted_reviews(self):
        self.recovered = True
        return []


def test_auto_review_scheduler_can_skip_recovery_by_env(monkeypatch):
    monkeypatch.setenv("CODE_REVIEW_DISABLE_AUTO_SCHEDULER", "1")
    service = _FakeReviewService()
    scheduler = AutoReviewScheduler(service)  # type: ignore[arg-type]

    scheduler.start()

    assert service.recovered is False
    assert scheduler._thread is None
