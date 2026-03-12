from __future__ import annotations

from pathlib import Path

from app.domain.models.message import ConversationMessage
from app.repositories.fs import read_json, write_json


class FileMessageRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _message_path(self, review_id: str) -> Path:
        return self.root / "reviews" / review_id / "messages.json"

    def append(self, message: ConversationMessage) -> ConversationMessage:
        messages = self.list(message.review_id)
        messages.append(message)
        write_json(
            self._message_path(message.review_id),
            [item.model_dump(mode="json") for item in messages],
        )
        return message

    def list(self, review_id: str) -> list[ConversationMessage]:
        path = self._message_path(review_id)
        if not path.exists():
            return []
        return [ConversationMessage.model_validate(item) for item in read_json(path)]

    def list_by_issue(self, review_id: str, issue_id: str) -> list[ConversationMessage]:
        return [item for item in self.list(review_id) if item.issue_id == issue_id]
