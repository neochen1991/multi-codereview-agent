from __future__ import annotations

from app.domain.models.event import ReviewEvent


def phase_event(review_id: str, phase: str, message: str) -> ReviewEvent:
    return ReviewEvent(review_id=review_id, event_type="phase_changed", phase=phase, message=message)
