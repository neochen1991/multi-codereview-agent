from __future__ import annotations

from pathlib import Path

from app.domain.models.feedback import FeedbackLabel
from app.repositories.storage_factory import StorageRepositoryFactory


class FeedbackLearnerService:
    """从历史审核和人工反馈中聚合专家质量指标。"""

    def __init__(self, storage_root: Path) -> None:
        """初始化审核、议题和反馈仓储。"""

        repository_factory = StorageRepositoryFactory(Path(storage_root))
        self.review_repo = repository_factory.create_review_repository()
        self.issue_repo = repository_factory.create_issue_repository()
        self.feedback_repo = repository_factory.create_feedback_repository()

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

    def build_quality_profiles(self) -> dict[str, dict[str, dict[str, object]]]:
        """根据历史人工反馈生成可直接用于主链调权的质量画像。"""

        reviews = self.review_repo.list()
        expert_stats: dict[str, dict[str, int]] = {}
        issue_type_stats: dict[str, dict[str, int]] = {}

        for review in reviews:
            issues = self.issue_repo.list(review.review_id)
            labels_by_issue = self._index_labels(self.feedback_repo.list(review.review_id))
            for issue in issues:
                outcome = self._issue_feedback_outcome(issue, labels_by_issue.get(issue.issue_id, []))
                self._accumulate_quality_stats(
                    expert_stats,
                    str(issue.primary_expert_id or (issue.participant_expert_ids[0] if issue.participant_expert_ids else "")).strip(),
                    outcome,
                )
                self._accumulate_quality_stats(
                    issue_type_stats,
                    str(issue.normalized_issue_type or "").strip().lower(),
                    outcome,
                )

        return {
            "experts": {
                key: self._build_profile_payload(key, value)
                for key, value in expert_stats.items()
                if key
            },
            "issue_types": {
                key: self._build_profile_payload(key, value)
                for key, value in issue_type_stats.items()
                if key
            },
        }

    def build_runtime_threshold_recommendations(
        self,
        current_thresholds: dict[str, object],
        *,
        quality_profiles: dict[str, dict[str, dict[str, object]]] | None = None,
    ) -> dict[str, object]:
        """基于误报画像给出阈值调优建议，但不自动写回运行时配置。"""

        profiles = quality_profiles or self.build_quality_profiles()
        high_false_positive_profiles = []
        for group_name in ("experts", "issue_types"):
            for key, payload in dict(profiles.get(group_name) or {}).items():
                sample_count = int(payload.get("sample_count") or 0)
                false_positive_rate = float(payload.get("false_positive_rate") or 0.0)
                if sample_count >= 3 and false_positive_rate >= 0.5:
                    high_false_positive_profiles.append(
                        {
                            "group": group_name,
                            "key": key,
                            "sample_count": sample_count,
                            "false_positive_rate": false_positive_rate,
                        }
                    )

        recommended = {
            "issue_confidence_threshold_p1": self._coerce_threshold(
                current_thresholds.get("issue_confidence_threshold_p1"),
                0.85,
            ),
            "issue_confidence_threshold_p2": self._coerce_threshold(
                current_thresholds.get("issue_confidence_threshold_p2"),
                0.8,
            ),
            "issue_confidence_threshold_p3": self._coerce_threshold(
                current_thresholds.get("issue_confidence_threshold_p3"),
                0.7,
            ),
            "hint_issue_confidence_threshold": self._coerce_threshold(
                current_thresholds.get("hint_issue_confidence_threshold"),
                0.85,
            ),
        }
        if high_false_positive_profiles:
            recommended["issue_confidence_threshold_p2"] = min(
                0.92,
                round(max(recommended["issue_confidence_threshold_p2"], 0.85), 2),
            )
            recommended["issue_confidence_threshold_p3"] = min(
                0.9,
                round(max(recommended["issue_confidence_threshold_p3"], 0.78), 2),
            )
            recommended["hint_issue_confidence_threshold"] = min(
                0.95,
                round(max(recommended["hint_issue_confidence_threshold"], 0.9), 2),
            )

        return {
            "applied": False,
            "should_tighten": bool(high_false_positive_profiles),
            "recommended_thresholds": recommended,
            "basis": high_false_positive_profiles,
            "reason": (
                "历史误报画像偏高，建议收紧 P2/P3 和提示类 issue 阈值。"
                if high_false_positive_profiles
                else "当前样本不足或误报率未达到自动建议门槛。"
            ),
        }

    def _index_labels(
        self, labels: list[FeedbackLabel]
    ) -> dict[str, list[FeedbackLabel]]:
        """把反馈标签按 issue_id 建立索引，便于统计。"""

        result: dict[str, list[FeedbackLabel]] = {}
        for label in labels:
            result.setdefault(label.issue_id, []).append(label)
        return result

    def _issue_feedback_outcome(self, issue, labels: list[FeedbackLabel]) -> str:
        label_values = {str(label.label or "").strip() for label in labels if str(label.label or "").strip()}
        if "false_positive" in label_values:
            return "false_positive"
        if "accepted_risk" in label_values or str(issue.human_decision or "").strip() == "approved":
            return "confirmed"
        return "unresolved"

    def _accumulate_quality_stats(
        self,
        container: dict[str, dict[str, int]],
        key: str,
        outcome: str,
    ) -> None:
        normalized = str(key or "").strip()
        if not normalized:
            return
        row = container.setdefault(
            normalized,
            {"sample_count": 0, "false_positive_count": 0, "confirmed_count": 0, "unresolved_count": 0},
        )
        if outcome == "false_positive":
            row["sample_count"] += 1
            row["false_positive_count"] += 1
        elif outcome == "confirmed":
            row["sample_count"] += 1
            row["confirmed_count"] += 1
        else:
            row["unresolved_count"] += 1

    def _build_profile_payload(self, key: str, stats: dict[str, int]) -> dict[str, object]:
        sample_count = int(stats.get("sample_count", 0))
        false_positive_count = int(stats.get("false_positive_count", 0))
        confirmed_count = int(stats.get("confirmed_count", 0))
        unresolved_count = int(stats.get("unresolved_count", 0))
        false_positive_rate = round(false_positive_count / sample_count, 2) if sample_count else 0.0
        confidence_penalty = 0.0
        needs_human_confidence = 0.8
        prefer_needs_verification = False
        if sample_count >= 3 and false_positive_rate >= 0.6:
            confidence_penalty = 0.12
            needs_human_confidence = 0.9
            prefer_needs_verification = True
        elif sample_count >= 3 and false_positive_rate >= 0.4:
            confidence_penalty = 0.07
            needs_human_confidence = 0.85
            prefer_needs_verification = True
        return {
            "key": key,
            "sample_count": sample_count,
            "false_positive_count": false_positive_count,
            "confirmed_count": confirmed_count,
            "unresolved_count": unresolved_count,
            "false_positive_rate": false_positive_rate,
            "confidence_penalty": confidence_penalty,
            "needs_human_confidence": needs_human_confidence,
            "prefer_needs_verification": prefer_needs_verification,
        }

    def _coerce_threshold(self, value: object, default: float) -> float:
        try:
            threshold = float(value)
        except (TypeError, ValueError):
            threshold = default
        return round(min(1.0, max(0.1, threshold)), 2)
