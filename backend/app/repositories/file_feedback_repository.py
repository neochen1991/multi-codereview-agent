from __future__ import annotations

from pathlib import Path

from app.domain.models.feedback import FeedbackLabel
from app.repositories.fs import read_json, write_json


class FileFeedbackRepository:
    """以文件方式保存人工反馈和学习标签。"""

    def __init__(self, root: Path) -> None:
        """初始化反馈存储根目录。"""

        self.root = Path(root)

    def _feedback_path(self, review_id: str) -> Path:
        """返回指定审核的反馈文件路径。"""

        return self.root / "reviews" / review_id / "feedback.json"

    def save(self, label: FeedbackLabel) -> FeedbackLabel:
        """追加保存一条反馈标签。"""

        labels = self.list(label.review_id)
        labels.append(label)
        write_json(
            self._feedback_path(label.review_id),
            [item.model_dump(mode="json") for item in labels],
        )
        return label

    def list(self, review_id: str) -> list[FeedbackLabel]:
        """读取指定审核的全部反馈标签。"""

        path = self._feedback_path(review_id)
        if not path.exists():
            return []
        return [FeedbackLabel.model_validate(item) for item in read_json(path)]
