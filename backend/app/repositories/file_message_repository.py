from __future__ import annotations

import threading
from pathlib import Path

from app.domain.models.message import ConversationMessage
from app.repositories.fs import read_json, write_json


class FileMessageRepository:
    """以文件方式保存审核过程中的对话消息。"""

    def __init__(self, root: Path) -> None:
        """初始化消息存储根目录和写入锁。"""

        self.root = Path(root)
        self._lock = threading.Lock()

    def _message_path(self, review_id: str) -> Path:
        """返回指定审核的消息文件路径。"""

        return self.root / "reviews" / review_id / "messages.json"

    def append(self, message: ConversationMessage) -> ConversationMessage:
        """向指定审核追加一条对话消息。"""

        with self._lock:
            messages = self.list(message.review_id)
            messages.append(message)
            write_json(
                self._message_path(message.review_id),
                [item.model_dump(mode="json") for item in messages],
            )
        return message

    def list(self, review_id: str) -> list[ConversationMessage]:
        """读取指定审核的完整消息列表。"""

        path = self._message_path(review_id)
        if not path.exists():
            return []
        return [ConversationMessage.model_validate(item) for item in read_json(path)]

    def list_by_issue(self, review_id: str, issue_id: str) -> list[ConversationMessage]:
        """读取指定议题对应的消息子集。"""

        return [item for item in self.list(review_id) if item.issue_id == issue_id]
