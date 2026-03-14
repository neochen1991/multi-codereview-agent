from __future__ import annotations

from pathlib import Path

from app.domain.models.feedback import FeedbackLabel
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewTask
from app.repositories.file_feedback_repository import FileFeedbackRepository
from app.repositories.file_issue_repository import FileIssueRepository
from app.repositories.file_review_repository import FileReviewRepository


class FeedbackLearnerService:
    """从历史审核和人工反馈中聚合专家质量指标。"""

    def __init__(self, storage_root: Path) -> None:
        """初始化审核、议题和反馈仓储。"""

        self.review_repo = FileReviewRepository(storage_root)
        self.issue_repo = FileIssueRepository(storage_root)
        self.feedback_repo = FileFeedbackRepository(storage_root)

    def build_expert_metrics(self) -> list[dict[str, object]]:
        """汇总每个专家的误报、人工批准和工具核验指标。"""

        reviews = self.review_repo.list()
        metrics: dict[str, dict[str, object]] = {}
        for review in reviews:
            issues = self.issue_repo.list(review.review_id)
            feedback_labels = self.feedback_repo.list(review.review_id)
            labels_by_issue = self._index_labels(feedback_labels)
            for issue in issues:
                for expert_id in issue.participant_expert_ids:
                    row = metrics.setdefault(
                        expert_id,
                        {
                            "expert_id": expert_id,
                            "issue_count": 0,
                            "tool_verified_count": 0,
                            "debated_issue_count": 0,
                            "accepted_risk_count": 0,
                            "false_positive_count": 0,
                            "human_approved_count": 0,
                        },
                    )
                    row["issue_count"] = int(row["issue_count"]) + 1
                    if issue.tool_verified:
                        row["tool_verified_count"] = int(row["tool_verified_count"]) + 1
                    if issue.needs_debate:
                        row["debated_issue_count"] = int(row["debated_issue_count"]) + 1
                    if issue.human_decision == "approved":
                        row["human_approved_count"] = int(row["human_approved_count"]) + 1
                    for label in labels_by_issue.get(issue.issue_id, []):
                        if label.label == "accepted_risk":
                            row["accepted_risk_count"] = int(row["accepted_risk_count"]) + 1
                        if label.label == "false_positive":
                            row["false_positive_count"] = int(row["false_positive_count"]) + 1
        return sorted(
            metrics.values(),
            key=lambda item: (
                -int(item["false_positive_count"]),
                -int(item["issue_count"]),
                str(item["expert_id"]),
            ),
        )

    def _index_labels(
        self, labels: list[FeedbackLabel]
    ) -> dict[str, list[FeedbackLabel]]:
        """把反馈标签按 issue_id 建立索引，便于统计。"""

        result: dict[str, list[FeedbackLabel]] = {}
        for label in labels:
            result.setdefault(label.issue_id, []).append(label)
        return result
