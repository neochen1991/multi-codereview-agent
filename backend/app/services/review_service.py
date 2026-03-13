from __future__ import annotations

import threading
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.domain.models.event import ReviewEvent
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.feedback import FeedbackLabel
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.knowledge import KnowledgeDocument
from app.domain.models.message import ConversationMessage
from app.domain.models.report import ReviewReport
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.file_event_repository import FileEventRepository
from app.repositories.file_feedback_repository import FileFeedbackRepository
from app.repositories.file_finding_repository import FileFindingRepository
from app.repositories.file_issue_repository import FileIssueRepository
from app.repositories.file_message_repository import FileMessageRepository
from app.repositories.file_review_repository import FileReviewRepository
from app.services.artifact_service import ArtifactService, build_report_summary
from app.services.expert_registry import ExpertRegistry
from app.services.feedback_learner_service import FeedbackLearnerService
from app.services.knowledge_service import KnowledgeService
from app.services.platform_adapter import PlatformAdapter
from app.services.repository_context_service import RepositoryContextService
from app.services.review_runner import ReviewRunner
from app.services.runtime_settings_service import RuntimeSettingsService


class ReviewService:
    def __init__(self, storage_root: Path | None = None) -> None:
        self.storage_root = Path(storage_root or settings.STORAGE_ROOT)
        self.review_repo = FileReviewRepository(self.storage_root)
        self.event_repo = FileEventRepository(self.storage_root)
        self.feedback_repo = FileFeedbackRepository(self.storage_root)
        self.finding_repo = FileFindingRepository(self.storage_root)
        self.issue_repo = FileIssueRepository(self.storage_root)
        self.message_repo = FileMessageRepository(self.storage_root)
        self.runner = ReviewRunner(self.storage_root)
        self.artifact_service = ArtifactService(self.storage_root)
        self.expert_registry = ExpertRegistry(self.storage_root / "experts")
        self.feedback_learner_service = FeedbackLearnerService(self.storage_root)
        self.knowledge_service = KnowledgeService(self.storage_root)
        self.runtime_settings_service = RuntimeSettingsService(self.storage_root)
        self.platform_adapter = PlatformAdapter()
        self._active_reviews: set[str] = set()
        self._active_reviews_lock = threading.Lock()

    def create_review(self, payload: dict[str, object]) -> ReviewTask:
        review_id = f"rev_{uuid4().hex[:8]}"
        runtime_settings = self.get_runtime_settings()
        selected_experts = [
            str(expert_id).strip()
            for expert_id in payload.pop("selected_experts", []) or []
            if str(expert_id).strip()
        ]
        if not str(payload.get("access_token") or "").strip() and (runtime_settings.code_repo_access_token or "").strip():
            payload["access_token"] = runtime_settings.code_repo_access_token
        subject = self.platform_adapter.normalize(ReviewSubject.model_validate(payload))
        task = ReviewTask(
            review_id=review_id,
            subject=subject,
            status="pending",
            phase="pending",
            selected_experts=selected_experts or settings.DEFAULT_EXPERT_IDS,
        )
        self.review_repo.save(task)
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="review_created",
                phase="pending",
                message="审核任务已创建",
            )
        )
        return task

    def start_review(self, review_id: str) -> ReviewTask:
        try:
            return self.runner.run_once(review_id)
        except Exception as exc:
            return self._mark_failed(review_id, str(exc))

    def start_review_async(self, review_id: str) -> ReviewTask:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return self.start_review(review_id)
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        if review.status in {"running", "waiting_human", "completed"}:
            return review
        with self._active_reviews_lock:
            if review_id in self._active_reviews:
                refreshed = self.get_review(review_id)
                return refreshed or review
            self._active_reviews.add(review_id)

        review.status = "running"
        review.phase = "queued"
        if review.started_at is None:
            review.started_at = datetime.now(UTC)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="review_queued",
                phase="queued",
                message="审核任务已进入执行队列",
            )
        )

        def _run_in_background() -> None:
            try:
                self.runner.run_once(review_id)
            except Exception as exc:
                self._mark_failed(review_id, str(exc))
            finally:
                with self._active_reviews_lock:
                    self._active_reviews.discard(review_id)

        threading.Thread(target=_run_in_background, daemon=True).start()
        return review

    def _mark_failed(self, review_id: str, reason: str) -> ReviewTask:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        review.status = "failed"
        review.phase = "failed"
        review.failure_reason = reason
        review.report_summary = f"审核失败：{reason}"
        if review.started_at is None:
            review.started_at = datetime.now(UTC)
        review.completed_at = datetime.now(UTC)
        review.duration_seconds = self._duration_seconds(review.started_at, review.completed_at)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="review_failed",
                phase="failed",
                message="代码审核任务执行失败",
                payload={"reason": reason},
            )
        )
        return review

    def _duration_seconds(self, started_at: datetime | None, completed_at: datetime | None) -> float | None:
        if started_at is None or completed_at is None:
            return None
        return max(0.0, round((completed_at - started_at).total_seconds(), 3))

    def get_review(self, review_id: str) -> ReviewTask | None:
        return self.review_repo.get(review_id)

    def list_reviews(self) -> list[ReviewTask]:
        return self.review_repo.list()

    def list_events(self, review_id: str) -> list[ReviewEvent]:
        return self.event_repo.list(review_id)

    def list_findings(self, review_id: str) -> list[ReviewFinding]:
        return self.finding_repo.list(review_id)

    def list_issues(self, review_id: str) -> list[DebateIssue]:
        return self.issue_repo.list(review_id)

    def list_issue_messages(self, review_id: str, issue_id: str) -> list[ConversationMessage]:
        return self.message_repo.list_by_issue(review_id, issue_id)

    def list_all_messages(self, review_id: str) -> list[ConversationMessage]:
        return self.message_repo.list(review_id)

    def list_experts(self) -> list[ExpertProfile]:
        return self.expert_registry.list_all()

    def create_expert(self, payload: dict[str, object]) -> ExpertProfile:
        return self.expert_registry.create(payload)

    def update_expert(self, expert_id: str, payload: dict[str, object]) -> ExpertProfile:
        return self.expert_registry.update(expert_id, payload)

    def list_knowledge(self) -> list[KnowledgeDocument]:
        return self.knowledge_service.list_documents()

    def list_feedback_labels(self, review_id: str) -> list[FeedbackLabel]:
        return self.feedback_repo.list(review_id)

    def get_runtime_settings(self) -> RuntimeSettings:
        return self.runtime_settings_service.get()

    def update_runtime_settings(self, payload: dict[str, object]) -> RuntimeSettings:
        return self.runtime_settings_service.update(payload)

    def build_repository_context_service(self) -> RepositoryContextService:
        runtime = self.get_runtime_settings()
        return RepositoryContextService(
            clone_url=runtime.code_repo_clone_url,
            local_path=runtime.code_repo_local_path,
            default_branch=runtime.code_repo_default_branch or runtime.default_target_branch,
            access_token=runtime.code_repo_access_token,
            auto_sync=runtime.code_repo_auto_sync,
        )

    def get_artifacts(self, review_id: str) -> dict[str, object]:
        try:
            return self.artifact_service.load(review_id)
        except KeyError:
            return {}

    def build_quality_metrics(self) -> dict[str, float | int]:
        reviews = self.list_reviews()
        total_issues = 0
        tool_verified = 0
        debated = 0
        surviving = 0
        needs_human = 0
        false_positive = 0
        for review in reviews:
            issues = self.list_issues(review.review_id)
            feedback_labels = self.list_feedback_labels(review.review_id)
            total_issues += len(issues)
            tool_verified += len([item for item in issues if item.tool_verified])
            debated += len([item for item in issues if item.needs_debate])
            surviving += len(
                [
                    item
                    for item in issues
                    if item.needs_debate and item.resolution in {"judge_accepted", "human_approved"}
                ]
            )
            needs_human += len([item for item in issues if item.needs_human])
            false_positive += len([item for item in feedback_labels if item.label == "false_positive"])
        denominator = total_issues or 1
        debated_denominator = debated or 1
        return {
            "review_count": len(reviews),
            "issue_count": total_issues,
            "tool_confirmation_rate": round(tool_verified / denominator, 2),
            "debate_survival_rate": round(surviving / debated_denominator, 2),
            "needs_human_count": needs_human,
            "false_positive_count": false_positive,
        }

    def build_expert_metrics(self) -> list[dict[str, object]]:
        return self.feedback_learner_service.build_expert_metrics()

    def create_knowledge_document(self, payload: dict[str, object]) -> KnowledgeDocument:
        return self.knowledge_service.create_document(payload)

    def retrieve_knowledge(
        self, expert_id: str, review_context: dict[str, object]
    ) -> list[KnowledgeDocument]:
        return self.knowledge_service.retrieve_for_expert(expert_id, review_context)

    def build_report(self, review_id: str) -> ReviewReport:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        findings = self.list_findings(review_id)
        issues = self.list_issues(review_id)
        issue_count = len({item.finding_id for item in findings})
        summary = (
            f"本次代码审核共收敛 {len(findings)} 条发现，"
            f"形成 {len(issues)} 个争议/裁决议题，"
            f"覆盖 {len(review.selected_experts)} 个专家视角，"
            f"当前状态为 {review.status}。"
        )
        return ReviewReport(
            review_id=review_id,
            status=review.status,
            phase=review.phase,
            summary=summary,
            review=review,
            findings=findings,
            issues=issues,
            issue_count=issue_count,
            human_review_status=review.human_review_status,
            confidence_summary={
                "high_confidence_count": len(
                    [item for item in findings if item.confidence >= 0.85]
                ),
                "debated_issue_count": len(
                    [item for item in issues if item.status in {"debating", "needs_human", "resolved"}]
                ),
                "needs_human_count": len([item for item in issues if item.needs_human]),
                "verified_issue_count": len([item for item in issues if item.verified]),
                "direct_defect_count": len([item for item in findings if item.finding_type == "direct_defect"]),
                "risk_hypothesis_count": len([item for item in findings if item.finding_type == "risk_hypothesis"]),
                "test_gap_count": len([item for item in findings if item.finding_type == "test_gap"]),
                "design_concern_count": len([item for item in findings if item.finding_type == "design_concern"]),
            },
        )

    def build_replay_bundle(self, review_id: str) -> dict[str, object]:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        return {
            "review": review.model_dump(mode="json"),
            "events": [item.model_dump(mode="json") for item in self.list_events(review_id)],
            "issues": [item.model_dump(mode="json") for item in self.list_issues(review_id)],
            "messages": [item.model_dump(mode="json") for item in self.list_all_messages(review_id)],
            "report": self.build_report(review_id).model_dump(mode="json"),
            "feedback_labels": [item.model_dump(mode="json") for item in self.list_feedback_labels(review_id)],
        }

    def record_human_decision(
        self, review_id: str, issue_id: str, decision: str, comment: str
    ) -> ReviewTask:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        issues = self.issue_repo.list(review_id)
        updated_issues: list[DebateIssue] = []
        touched = False
        for issue in issues:
            if issue.issue_id != issue_id:
                updated_issues.append(issue)
                continue
            touched = True
            updated_issues.append(
                issue.model_copy(
                    update={
                        "human_decision": decision,
                        "status": "resolved",
                        "resolution": f"human_{decision}",
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
        if not touched:
            raise KeyError(issue_id)
        self.issue_repo.save_all(review_id, updated_issues)
        feedback_label = "accepted_risk" if decision == "approved" else "false_positive"
        self.feedback_repo.save(
            FeedbackLabel(
                review_id=review_id,
                issue_id=issue_id,
                label=feedback_label,
                comment=comment,
            )
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id=issue_id,
                expert_id="human_reviewer",
                message_type="human_comment",
                content=comment,
                metadata={"decision": decision},
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="human_decision_recorded",
                phase="human_gate",
                message=f"人工审核已{decision}",
                payload={"issue_id": issue_id, "decision": decision},
            )
        )
        pending_ids = [item.issue_id for item in updated_issues if item.needs_human and item.status != "resolved"]
        review.status = "completed" if not pending_ids else "waiting_human"
        review.phase = "completed" if not pending_ids else "human_gate"
        review.human_review_status = decision if not pending_ids else "requested"
        review.pending_human_issue_ids = pending_ids
        if not pending_ids:
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = self._duration_seconds(review.started_at or review.created_at, review.completed_at)
        review.report_summary = build_report_summary(
            review=review,
            finding_count=len(self.list_findings(review_id)),
            issue_count=len(updated_issues),
            pending_human_count=len(pending_ids),
        )
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.artifact_service.publish(review, updated_issues)
        if not pending_ids:
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="review_completed",
                    phase="completed",
                    message="人工裁决后审核完成",
                )
            )
        return review


review_service = ReviewService()
