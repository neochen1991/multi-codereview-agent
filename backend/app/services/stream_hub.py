from __future__ import annotations

from collections.abc import Iterable

from app.domain.models.event import ReviewEvent


def encode_sse_event(event: ReviewEvent) -> str:
    """把单个事件编码成 SSE 文本块。"""

    return f"event: {event.event_type}\n" f"data: {event.model_dump_json()}\n\n"


def encode_sse(events: Iterable[ReviewEvent]) -> str:
    """把多个事件串联编码成 SSE 响应体。"""

    chunks: list[str] = []
    for event in events:
        chunks.append(encode_sse_event(event))
    return "".join(chunks)
