from __future__ import annotations

from pathlib import Path

from app.domain.models.issue import DebateIssue
from app.repositories.fs import read_json, write_json


class FileIssueRepository:
    """以文件方式保存议题合并后的 issue 列表。"""

    def __init__(self, root: Path) -> None:
        """初始化 issue 存储根目录。"""

        self.root = Path(root)

    def _issue_path(self, review_id: str) -> Path:
        """返回指定审核的 issue 文件路径。"""

        return self.root / "reviews" / review_id / "issues.json"

    def save_all(self, review_id: str, issues: list[DebateIssue]) -> list[DebateIssue]:
        """整体覆盖保存某次审核的全部 issue。"""

        write_json(
            self._issue_path(review_id),
            [item.model_dump(mode="json") for item in issues],
        )
        return issues

    def list(self, review_id: str) -> list[DebateIssue]:
        """读取指定审核的全部 issue。"""

        path = self._issue_path(review_id)
        if not path.exists():
            return []
        return [DebateIssue.model_validate(item) for item in read_json(path)]
