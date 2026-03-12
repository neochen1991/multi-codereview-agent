from __future__ import annotations

from pathlib import Path

from app.domain.models.feedback import FeedbackLabel
from app.repositories.fs import read_json, write_json


class FileFeedbackRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _feedback_path(self, review_id: str) -> Path:
        return self.root / "reviews" / review_id / "feedback.json"

    def save(self, label: FeedbackLabel) -> FeedbackLabel:
        labels = self.list(label.review_id)
        labels.append(label)
        write_json(
            self._feedback_path(label.review_id),
            [item.model_dump(mode="json") for item in labels],
        )
        return label

    def list(self, review_id: str) -> list[FeedbackLabel]:
        path = self._feedback_path(review_id)
        if not path.exists():
            return []
        return [FeedbackLabel.model_validate(item) for item in read_json(path)]
