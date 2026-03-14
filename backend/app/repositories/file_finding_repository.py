from __future__ import annotations

import threading
from pathlib import Path

from app.domain.models.finding import ReviewFinding
from app.repositories.fs import read_json, write_json


class FileFindingRepository:
    """以文件方式存储结构化审核发现。"""

    def __init__(self, root: Path) -> None:
        """初始化 finding 存储根目录和写入锁。"""

        self.root = Path(root)
        self._lock = threading.Lock()

    def _finding_path(self, review_id: str) -> Path:
        """返回指定审核的 finding 文件路径。"""

        return self.root / "reviews" / review_id / "findings.json"

    def save(self, review_id: str, finding: ReviewFinding) -> ReviewFinding:
        """向指定审核追加一条 finding。"""

        with self._lock:
            findings = self.list(review_id)
            findings.append(finding)
            write_json(
                self._finding_path(review_id),
                [item.model_dump(mode="json") for item in findings],
            )
        return finding

    def list(self, review_id: str) -> list[ReviewFinding]:
        """读取指定审核的全部 finding。"""

        path = self._finding_path(review_id)
        if not path.exists():
            return []
        payload = read_json(path)
        return [ReviewFinding.model_validate(item) for item in payload]
