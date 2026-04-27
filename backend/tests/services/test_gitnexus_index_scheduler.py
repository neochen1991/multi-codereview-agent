from pathlib import Path

from app.services.gitnexus_index_scheduler import GitNexusIndexScheduler
from app.services.review_service import ReviewService


def test_gitnexus_index_scheduler_skips_without_repo_path(storage_root: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    service = ReviewService(storage_root=storage_root)
    scheduler = GitNexusIndexScheduler(service)

    status = scheduler.tick()

    assert status["state"] == "skipped"
    assert "本地代码仓路径" in str(status["message"])
