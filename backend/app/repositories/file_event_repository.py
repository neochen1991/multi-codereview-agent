from __future__ import annotations

from pathlib import Path

from app.domain.models.event import ReviewEvent
from app.repositories.fs import read_json, write_json


class FileEventRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _event_path(self, review_id: str) -> Path:
        return self.root / "reviews" / review_id / "events.json"

    def append(self, event: ReviewEvent) -> ReviewEvent:
        events = self.list(event.review_id)
        events.append(event)
        write_json(
            self._event_path(event.review_id),
            [item.model_dump(mode="json") for item in events],
        )
        return event

    def list(self, review_id: str) -> list[ReviewEvent]:
        path = self._event_path(review_id)
        if not path.exists():
            return []
        payload = read_json(path)
        return [ReviewEvent.model_validate(item) for item in payload]
