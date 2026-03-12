from __future__ import annotations

from pathlib import Path

from app.domain.models.issue import DebateIssue
from app.repositories.fs import read_json, write_json


class FileIssueRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _issue_path(self, review_id: str) -> Path:
        return self.root / "reviews" / review_id / "issues.json"

    def save_all(self, review_id: str, issues: list[DebateIssue]) -> list[DebateIssue]:
        write_json(
            self._issue_path(review_id),
            [item.model_dump(mode="json") for item in issues],
        )
        return issues

    def list(self, review_id: str) -> list[DebateIssue]:
        path = self._issue_path(review_id)
        if not path.exists():
            return []
        return [DebateIssue.model_validate(item) for item in read_json(path)]

