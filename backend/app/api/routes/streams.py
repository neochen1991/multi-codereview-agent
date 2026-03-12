from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.domain.models.event import ReviewEvent
from app.services.review_service import review_service
from app.services.stream_hub import encode_sse, encode_sse_event

router = APIRouter()


@router.get("/reviews/{review_id}/events")
def list_events(review_id: str) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in review_service.list_events(review_id)]


@router.get("/reviews/{review_id}/events/stream")
async def stream_events(review_id: str, request: Request) -> StreamingResponse:
    review = review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    async def event_generator():
        previous_event_count = len(review_service.list_events(review_id))
        previous_message_count = len(review_service.list_all_messages(review_id))
        idle_rounds_after_finish = 0

        while True:
            if await request.is_disconnected():
                break

            current_review = review_service.get_review(review_id)
            if current_review is None:
                break

            events = review_service.list_events(review_id)
            messages = review_service.list_all_messages(review_id)
            emitted = False

            if len(events) > previous_event_count:
                for event in events[previous_event_count:]:
                    yield encode_sse_event(event)
                previous_event_count = len(events)
                emitted = True

            if len(messages) > previous_message_count:
                latest = messages[-1]
                yield encode_sse_event(
                    ReviewEvent(
                        review_id=review_id,
                        event_type="message_update",
                        phase=current_review.phase,
                        message="对话流已更新",
                        payload={
                            "message_count": len(messages),
                            "latest_message_id": latest.message_id,
                            "latest_message_type": latest.message_type,
                            "latest_expert_id": latest.expert_id,
                        },
                    )
                )
                previous_message_count = len(messages)
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
