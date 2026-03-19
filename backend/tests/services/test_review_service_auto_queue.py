from pathlib import Path

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
