from pathlib import Path

from app.domain.models.review import ReviewTask
from app.services.platform_adapter import OpenMergeRequest
from app.services.review_service import ReviewService


def test_enqueue_open_merge_requests_creates_pending_reviews_and_deduplicates(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    service.platform_adapter.normalize = lambda subject, runtime_settings=None: subject.model_copy(  # type: ignore[method-assign]
        update={
            "repo_id": subject.repo_id or "projectname",
            "project_id": subject.project_id or "FND",
            "source_ref": subject.source_ref or "mr/demo",
            "target_ref": subject.target_ref or "main",
            "title": subject.title or "Auto MR",
        }
    )
    service.platform_adapter.list_open_merge_requests = lambda repo_url, access_token, runtime_settings=None: [  # type: ignore[method-assign]
        OpenMergeRequest(
            mr_url="https://codehub-g.huawei.com/PIP/FND/projectname/merge_requests/101",
            title="MR 101",
            source_ref="feature/mr-101",
            target_ref="main",
            number="101",
            head_sha="abc101",
        ),
        OpenMergeRequest(
            mr_url="https://codehub-g.huawei.com/PIP/FND/projectname/merge_requests/102",
            title="MR 102",
            source_ref="feature/mr-102",
            target_ref="main",
            number="102",
            head_sha="abc102",
        ),
    ]

    created = service.enqueue_open_merge_requests("codehub-g.huawei.com/PIP/FND/projectname/merge_requests")
    assert len(created) == 2
    assert all(item.status == "pending" for item in created)

    duplicated = service.enqueue_open_merge_requests("codehub-g.huawei.com/PIP/FND/projectname/merge_requests")
    assert duplicated == []

    queue = service.list_pending_queue()
    assert len(queue) == 2
    assert queue[0].created_at <= queue[1].created_at


def test_start_next_pending_review_recovers_interrupted_running_review(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    service.platform_adapter.normalize = lambda subject, runtime_settings=None: subject.model_copy(  # type: ignore[method-assign]
        update={
            "repo_id": subject.repo_id or "projectname",
            "project_id": subject.project_id or "FND",
            "source_ref": subject.source_ref or "mr/demo",
            "target_ref": subject.target_ref or "main",
            "title": subject.title or "Auto MR",
        }
    )
    stale = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "projectname",
            "project_id": "FND",
            "source_ref": "feature/stale",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/10",
            "title": "stale running review",
        }
    )
    stale.status = "running"
    stale.phase = "expert_review"
    service.review_repo.save(stale)

    next_pending = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "projectname",
            "project_id": "FND",
            "source_ref": "feature/pending",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/11",
            "title": "next pending review",
        }
    )

    started_ids: list[str] = []

    def fake_start_review_async(review_id: str) -> ReviewTask:
        started_ids.append(review_id)
        review = service.get_review(review_id)
        assert review is not None
        review.status = "running"
        review.phase = "queued"
        service.review_repo.save(review)
        return review

    service.start_review_async = fake_start_review_async  # type: ignore[method-assign]

    started = service.start_next_pending_review()

    assert started is not None
    assert started.review_id == stale.review_id
    assert started_ids == [stale.review_id]
    recovered = service.get_review(stale.review_id)
    assert recovered is not None
    assert recovered.status == "running"
    events = service.list_events(stale.review_id)
    assert any(item.event_type == "review_recovered" for item in events)
    queue_ids = [item.review_id for item in service.list_pending_queue()]
    assert next_pending.review_id in queue_ids
