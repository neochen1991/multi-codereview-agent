from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.domain.models.event import ReviewEvent
import app.services.review_service as review_service_module
from app.services.stream_hub import encode_sse_event

router = APIRouter()


@router.get("/reviews/{review_id}/events")
def list_events(
    review_id: str,
    since: str = "",
    limit: int = 0,
) -> list[dict[str, object]]:
    """返回某次审核当前已落盘的事件列表。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_events(review_id, since=since, limit=limit)
    ]


@router.get("/reviews/{review_id}/events/stream")
async def stream_events(review_id: str, request: Request) -> StreamingResponse:
    """通过 SSE 实时推送审核事件和消息更新。"""

    review = review_service_module.review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    async def event_generator():
        """循环监听新增事件和消息，并按 SSE 协议输出。"""

        seed_events = review_service_module.review_service.list_events(review_id)
        seed_messages = review_service_module.review_service.list_all_messages(review_id)
        event_since = str(seed_events[-1].created_at) if seed_events else ""
        message_since = str(seed_messages[-1].created_at) if seed_messages else ""
        seen_event_ids: set[str] = set()
        seen_message_ids: set[str] = set()
        idle_rounds_after_finish = 0

        while True:
            if await request.is_disconnected():
                break

            current_review = review_service_module.review_service.get_review(review_id)
            if current_review is None:
                break

            events = review_service_module.review_service.list_events(
                review_id,
                since=event_since,
                limit=500,
            )
            messages = review_service_module.review_service.list_all_messages(
                review_id,
                since=message_since,
                limit=500,
            )
            emitted = False

            new_events = [event for event in events if event.event_id not in seen_event_ids]
            if new_events:
                for event in new_events:
                    seen_event_ids.add(event.event_id)
                    yield encode_sse_event(event)
                event_since = str(new_events[-1].created_at)
                emitted = True

            new_messages = [message for message in messages if message.message_id not in seen_message_ids]
            if new_messages:
                latest = new_messages[-1]
                for message in new_messages:
                    seen_message_ids.add(message.message_id)
                message_since = str(latest.created_at)
                yield encode_sse_event(
                    ReviewEvent(
                        review_id=review_id,
                        event_type="message_update",
                        phase=current_review.phase,
                        message="对话流已更新",
                        payload={
                            "message_delta_count": len(new_messages),
                            "latest_message_id": latest.message_id,
                            "latest_message_type": latest.message_type,
                            "latest_expert_id": latest.expert_id,
                        },
                    )
                )
                emitted = True

            if not emitted:
                yield ": keep-alive\n\n"

            if current_review.status in {"completed", "failed"}:
                idle_rounds_after_finish = 0 if emitted else idle_rounds_after_finish + 1
                if idle_rounds_after_finish >= 4:
                    break

            await asyncio.sleep(0.75)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
