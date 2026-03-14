from __future__ import annotations

import threading
from pathlib import Path

from app.domain.models.event import ReviewEvent
from app.repositories.fs import read_json, write_json


class FileEventRepository:
    """以文件方式保存审核事件时间线。"""

    def __init__(self, root: Path) -> None:
        """初始化事件存储根目录和写入锁。"""

        self.root = Path(root)
        self._lock = threading.Lock()

    def _event_path(self, review_id: str) -> Path:
        """返回某次审核对应的事件文件路径。"""

        return self.root / "reviews" / review_id / "events.json"

    def append(self, event: ReviewEvent) -> ReviewEvent:
        """向指定审核的事件流追加一条事件。"""

        with self._lock:
            events = self.list(event.review_id)
            events.append(event)
            write_json(
                self._event_path(event.review_id),
                [item.model_dump(mode="json") for item in events],
            )
        return event

    def list(self, review_id: str) -> list[ReviewEvent]:
        """读取指定审核的全部事件列表。"""

        path = self._event_path(review_id)
        if not path.exists():
            return []
        payload = read_json(path)
        return [ReviewEvent.model_validate(item) for item in payload]
