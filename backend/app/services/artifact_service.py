from __future__ import annotations

import shutil
from pathlib import Path

from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewTask
from app.repositories.fs import read_json, write_json


def build_report_summary(
    review: ReviewTask,
    finding_count: int,
    issue_count: int,
    pending_human_count: int,
) -> str:
    """生成产物快照和结果页都会复用的报告摘要文案。"""

    return (
        f"审核报告已生成，共收敛 {finding_count} 条 findings，"
        f"形成 {issue_count} 个议题，其中 {pending_human_count} 个待人工裁决。"
    )


class ArtifactService:
    """负责发布和读取审核对外产物快照。"""

    def __init__(self, storage_root: Path) -> None:
        """初始化产物目录根路径。"""

        self.storage_root = Path(storage_root)

    def publish(self, review: ReviewTask, issues: list[DebateIssue]) -> dict[str, object]:
        """落盘 summary comment、check run 和报告快照。"""

        artifact_dir = self.storage_root / "reviews" / review.review_id / "artifacts"
        summary_comment = {
            "review_id": review.review_id,
            "title": review.subject.title or review.review_id,
            "summary": review.report_summary,
            "issue_count": len(issues),
            "human_review_status": review.human_review_status,
        }
        check_run = {
            "name": "multi-agent-code-review",
            "status": review.status,
            "conclusion": "action_required" if review.human_review_status == "requested" else "completed",
            "details_url": f"/review/{review.review_id}",
            "issues": [issue.issue_id for issue in issues],
        }
        report_snapshot = {
            "review_id": review.review_id,
            "status": review.status,
            "phase": review.phase,
            "pending_human_issue_ids": review.pending_human_issue_ids,
            "updated_at": review.updated_at,
        }
        write_json(artifact_dir / "summary_comment.json", summary_comment)
        write_json(artifact_dir / "check_run.json", check_run)
        write_json(artifact_dir / "report_snapshot.json", report_snapshot)
        return {
            "summary_comment": summary_comment,
            "check_run": check_run,
            "report_snapshot": report_snapshot,
        }

    def load(self, review_id: str) -> dict[str, object]:
        """读取指定审核的产物快照。"""

        artifact_dir = self.storage_root / "reviews" / review_id / "artifacts"
        if not artifact_dir.exists():
            raise KeyError(review_id)
        payload: dict[str, object] = {}
        for name in ("summary_comment", "check_run", "report_snapshot"):
            path = artifact_dir / f"{name}.json"
            if path.exists():
                payload[name] = read_json(path)
        if not payload:
            raise KeyError(review_id)
        return payload

    def clear(self, review_id: str) -> None:
        """删除某次审核历史运行产物，便于 failed 重跑时重新生成快照。"""

        artifact_dir = self.storage_root / "reviews" / review_id / "artifacts"
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir, ignore_errors=True)
