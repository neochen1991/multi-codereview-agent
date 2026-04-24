from __future__ import annotations

from datetime import datetime

from app.domain.models.review import ReviewTask
from app.services.platform_adapter import OpenMergeRequest


def pending_sort_key(review: ReviewTask) -> tuple[int, float]:
    metadata = dict(review.subject.metadata or {})
    priority_at = str(metadata.get("queue_priority_at") or "").strip()
    if priority_at:
        try:
            return (0, -datetime.fromisoformat(priority_at.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return (0, -updated_at_or_created_at(review.model_dump(mode="json")))
    return (1, created_at_timestamp(review.model_dump(mode="json")))


def pending_sort_key_from_payload(review: dict[str, object]) -> tuple[int, float]:
    metadata = {}
    subject = review.get("subject")
    if isinstance(subject, dict):
        raw_metadata = subject.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata = dict(raw_metadata)
    priority_at = str(metadata.get("queue_priority_at") or "").strip()
    if priority_at:
        try:
            return (0, -datetime.fromisoformat(priority_at.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return (0, -updated_at_or_created_at(review))
    return (1, created_at_timestamp(review))


def started_at_or_created_at(review: dict[str, object]) -> float:
    started_at = str(review.get("started_at") or "").strip()
    if started_at:
        try:
            return datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return created_at_timestamp(review)


def updated_at_or_created_at(review: dict[str, object]) -> float:
    updated_at = str(review.get("updated_at") or "").strip()
    if updated_at:
        try:
            return datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return created_at_timestamp(review)


def created_at_timestamp(review: dict[str, object]) -> float:
    created_at = str(review.get("created_at") or "").strip()
    if created_at:
        try:
            return datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0.0


def build_pending_queue_diagnostics(
    pending_reviews: list[dict[str, object]],
    active_running_review_id: str = "",
) -> list[dict[str, object]]:
    response: list[dict[str, object]] = []
    for index, item in enumerate(pending_reviews):
        blocker_code = "ready"
        blocker_message = "已满足启动条件，等待调度器拉起审核任务。"
        if active_running_review_id and index == 0:
            blocker_code = "blocked_by_running_review"
            blocker_message = f"前序任务 {active_running_review_id} 正在执行，本任务会在它结束后自动启动。"
        elif active_running_review_id:
            blocker_code = "waiting_for_turn_and_running_review"
            blocker_message = f"当前有任务 {active_running_review_id} 正在执行，且前方还有 {index} 条待处理任务，本任务需继续排队。"
        elif index > 0:
            blocker_code = "waiting_for_turn"
            blocker_message = f"前方还有 {index} 条待处理任务，本任务会按顺序自动启动。"
        response.append(
            item
            | {
                "queue_position": index + 1,
                "is_next_candidate": index == 0 and not active_running_review_id,
                "queue_blocker_code": blocker_code,
                "queue_blocker_message": blocker_message,
                "blocking_review_id": active_running_review_id,
            }
        )
    return response


def existing_auto_queue_keys(reviews: list[ReviewTask]) -> set[str]:
    keys: set[str] = set()
    for review in reviews:
        metadata = review.subject.metadata or {}
        has_auto_key = False
        if isinstance(metadata, dict):
            auto_key = str(metadata.get("auto_queue_key") or "").strip()
            if auto_key:
                keys.add(auto_key)
                has_auto_key = True
        mr_url = str(review.subject.mr_url or "").strip()
        if mr_url and not has_auto_key:
            keys.add(f"url:{mr_url}")
    return keys


def auto_queue_key(merge_request: OpenMergeRequest) -> str:
    if merge_request.head_sha:
        return f"url:{merge_request.mr_url}#sha:{merge_request.head_sha}"
    return f"url:{merge_request.mr_url}"
