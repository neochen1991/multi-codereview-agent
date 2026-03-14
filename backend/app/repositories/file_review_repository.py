from __future__ import annotations

from pathlib import Path

from app.domain.models.review import ReviewTask
from app.repositories.fs import read_json, write_json


class FileReviewRepository:
    """以文件方式持久化审核任务主记录。"""

    def __init__(self, root: Path) -> None:
        """初始化审核任务存储根目录。"""

        self.root = Path(root)

    def _review_path(self, review_id: str) -> Path:
        """返回指定审核任务的主记录路径。"""

        return self.root / "reviews" / review_id / "review.json"

    def save(self, task: ReviewTask) -> ReviewTask:
        """保存审核任务主记录。"""

        write_json(self._review_path(task.review_id), task.model_dump(mode="json"))
        return task

    def get(self, review_id: str) -> ReviewTask | None:
        """读取单个审核任务，不存在时返回空。"""

        path = self._review_path(review_id)
        if not path.exists():
            return None
        return ReviewTask.model_validate(read_json(path))

    def list(self) -> list[ReviewTask]:
        """列出所有审核任务，并按更新时间倒序返回。"""

        reviews_dir = self.root / "reviews"
        if not reviews_dir.exists():
            return []
        items: list[ReviewTask] = []
        for path in sorted(reviews_dir.glob("*/review.json")):
            items.append(ReviewTask.model_validate(read_json(path)))
        return sorted(items, key=lambda item: item.updated_at, reverse=True)
