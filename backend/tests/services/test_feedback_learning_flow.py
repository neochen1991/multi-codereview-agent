import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.models.finding import ReviewFinding
from app.domain.models.event import ReviewEvent
from app.domain.models.issue import DebateIssue
from app.repositories.fs import read_json

from app.services.review_service import ReviewService
from app.services.feedback_learner_service import FeedbackLearnerService
from app.services.review_learning_service import ReviewLearningService


def _seed_pending_human_issue(service: ReviewService, review_id: str) -> DebateIssue:
    issue = DebateIssue(
        review_id=review_id,
        issue_id="iss_seeded_human",
        title="高风险权限问题",
        summary="需要人工确认。",
        file_path="backend/app/security/authz.py",
        line_start=12,
        status="needs_human",
        severity="high",
        confidence=0.9,
        finding_ids=["fdg_seeded"],
        participant_expert_ids=["security_compliance"],
        needs_human=True,
    )
    service.issue_repo.save_all(review_id, [issue])
    review = service.get_review(review_id)
    assert review is not None
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [issue.issue_id]
    service.review_repo.save(review)
    return issue


def test_human_decision_persists_feedback_label(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/risk-guard",
            "target_ref": "main",
            "title": "security migration review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
            }
        )
    issue = _seed_pending_human_issue(service, review.review_id)

    service.record_human_decision(review.review_id, issue.issue_id, "rejected", "误报")
    labels = service.list_feedback_labels(review.review_id)

    assert labels
    assert labels[0].label == "false_positive"


def test_human_rejection_records_review_learning_case(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_java",
            "project_id": "proj_java",
            "source_ref": "feature/comment-contract",
            "target_ref": "dev",
            "title": "comment contract review",
            "changed_files": ["src/main/java/com/acme/order/OrderRepository.java"],
        }
    )
    issue = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_contract_false_positive",
        title="接口声明的方法未在实现类落地",
        summary="OrderRepository 接口定义了 findActive 方法，但实现类没有实际实现。",
        file_path="src/main/java/com/acme/order/OrderRepository.java",
        line_start=18,
        status="needs_human",
        severity="medium",
        confidence=0.82,
        participant_expert_ids=["correctness_business"],
        primary_expert_id="correctness_business",
        normalized_issue_type="comment_contract_unimplemented",
        evidence=[
            "接口 OrderRepository 声明 findActive(Long userId)。",
            "Tree-sitter 上下文显示 JdbcOrderRepository implements OrderRepository。",
            "JdbcOrderRepository 中存在 @Override public List<Order> findActive(Long userId) { return jdbc.query(...); }。",
        ],
        cross_file_evidence=["src/main/java/com/acme/order/JdbcOrderRepository.java 已实现该接口方法。"],
        context_files=["src/main/java/com/acme/order/JdbcOrderRepository.java"],
        needs_human=True,
    )
    service.issue_repo.save_all(review.review_id, [issue])
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [issue.issue_id]
    service.review_repo.save(review)

    service.record_human_decision(
        review.review_id,
        issue.issue_id,
        "rejected",
        "误报：实现类 JdbcOrderRepository 已 implements OrderRepository，并且 @Override 了 findActive。",
    )

    cases = ReviewLearningService(storage_root).list_cases(repo_id="repo_java")
    assert len(cases) == 1
    assert cases[0]["issue_id"] == issue.issue_id
    assert cases[0]["reason_category"] == "context_counterexample"


def test_human_approval_records_review_learning_case(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_java",
            "project_id": "proj_java",
            "source_ref": "feature/auth-filter",
            "target_ref": "dev",
            "title": "auth filter review",
            "changed_files": ["src/main/java/com/acme/order/OrderRepository.java"],
        }
    )
    issue = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_auth_confirmed",
        title="查询缺少当前用户过滤",
        summary="queryActive 没有按当前用户过滤。",
        file_path="src/main/java/com/acme/order/OrderRepository.java",
        line_start=18,
        status="needs_human",
        severity="high",
        confidence=0.86,
        participant_expert_ids=["security_compliance"],
        primary_expert_id="security_compliance",
        normalized_issue_type="missing_auth_check",
        evidence=["新增 queryActive 方法没有传入 userId 条件。"],
        context_files=["src/main/java/com/acme/order/OrderController.java"],
        needs_human=True,
    )
    service.issue_repo.save_all(review.review_id, [issue])
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [issue.issue_id]
    service.review_repo.save(review)

    service.record_human_decision(
        review.review_id,
        issue.issue_id,
        "approved",
        "确认问题成立：缺少当前用户过滤会导致越权查询。",
    )

    cases = ReviewLearningService(storage_root).list_cases(repo_id="repo_java", issue_type="missing_auth_check")
    assert len(cases) == 1
    assert cases[0]["decision"] == "approved"
    assert cases[0]["reason_category"] == "confirmed_risk"


def test_record_impact_feedback_persists_normalized_label_and_metadata(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/impact-feedback",
            "target_ref": "main",
            "title": "impact feedback review",
            "changed_files": ["backend/app/orders/service.py"],
        }
    )

    label = service.record_impact_feedback(
        review.review_id,
        target_type="impact_path",
        target_key="OrderController -> OrderService -> PaymentClient",
        label="false_positive",
        comment="实际没有走这个调用路径",
    )
    labels = service.list_feedback_labels(review.review_id)

    assert label.label == "impact_false_positive"
    assert label.source == "impact_feedback"
    assert label.issue_id.startswith("impact:impact_path:")
    assert labels == [label]
    payload = json.loads(label.comment)
    assert payload["target_key"] == "OrderController -> OrderService -> PaymentClient"
    assert payload["comment"] == "实际没有走这个调用路径"


def test_feedback_learner_builds_impact_feedback_profiles(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    learner = FeedbackLearnerService(storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/impact-profile",
            "target_ref": "main",
            "title": "impact feedback profile review",
            "changed_files": ["backend/app/orders/service.py"],
        }
    )

    service.record_impact_feedback(
        review.review_id,
        target_type="impact_path",
        target_key="OrderController -> OrderService -> PaymentClient",
        label="confirmed",
        comment="真实命中",
    )
    service.record_impact_feedback(
        review.review_id,
        target_type="impact_path",
        target_key="OrderController -> OrderService -> PaymentClient",
        label="false_positive",
        comment="另一次验证发现不命中",
    )

    profiles = learner.build_impact_feedback_profiles()
    target_profile = profiles["targets"]["impact_path:OrderController -> OrderService -> PaymentClient"]

    assert profiles["summary"]["sample_count"] == 2
    assert target_profile["confirmed_count"] == 1
    assert target_profile["false_positive_count"] == 1
    assert target_profile["false_positive_rate"] == 0.5
    assert target_profile["recommended_action"] == "keep_observing"


def test_feedback_learner_builds_quality_profiles(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    learner = FeedbackLearnerService(storage_root)

    for index in range(3):
        review = service.create_review(
            {
                "subject_type": "mr",
                "repo_id": f"repo_{index}",
                "project_id": f"proj_{index}",
                "source_ref": f"feature/{index}",
                "target_ref": "main",
                "title": f"quality profile review {index}",
                "changed_files": ["backend/app/security/authz.py"],
            }
        )
        issue = DebateIssue(
            review_id=review.review_id,
            issue_id=f"iss_profile_{index}",
            title="权限校验问题",
            summary="需要人工确认。",
            file_path="backend/app/security/authz.py",
            line_start=12,
            status="needs_human",
            severity="high",
            confidence=0.9,
            participant_expert_ids=["security_compliance"],
            primary_expert_id="security_compliance",
            normalized_issue_type="missing_auth_check",
            needs_human=True,
        )
        service.issue_repo.save_all(review.review_id, [issue])
        review.status = "waiting_human"
        review.phase = "human_gate"
        review.human_review_status = "requested"
        review.pending_human_issue_ids = [issue.issue_id]
        service.review_repo.save(review)
        service.record_human_decision(review.review_id, issue.issue_id, "rejected", "误报")

    profiles = learner.build_quality_profiles()

    expert_profile = profiles["experts"]["security_compliance"]
    issue_type_profile = profiles["issue_types"]["missing_auth_check"]
    assert expert_profile["sample_count"] == 3
    assert expert_profile["false_positive_rate"] == 1.0
    assert expert_profile["accept_rate"] == 0.0
    assert expert_profile["confidence_penalty"] > 0
    assert expert_profile["confidence_bonus"] == 0.0
    assert expert_profile["prefer_needs_verification"] is True
    assert issue_type_profile["sample_count"] == 3
    assert issue_type_profile["false_positive_rate"] == 1.0


def test_feedback_learner_builds_runtime_threshold_recommendations(storage_root: Path):
    learner = FeedbackLearnerService(storage_root)

    recommendations = learner.build_runtime_threshold_recommendations(
        {
            "issue_confidence_threshold_p1": 0.85,
            "issue_confidence_threshold_p2": 0.8,
            "issue_confidence_threshold_p3": 0.7,
            "hint_issue_confidence_threshold": 0.85,
        },
        quality_profiles={
            "experts": {
                "correctness_business": {
                    "sample_count": 5,
                    "false_positive_rate": 0.6,
                }
            },
            "issue_types": {
                "naming_violation": {
                    "sample_count": 4,
                    "false_positive_rate": 0.75,
                }
            },
        },
    )

    assert recommendations["should_tighten"] is True
    assert recommendations["recommended_thresholds"]["issue_confidence_threshold_p2"] == 0.85
    assert recommendations["recommended_thresholds"]["hint_issue_confidence_threshold"] == 0.9
    assert recommendations["applied"] is False


def test_human_decision_refreshes_report_summary_and_artifacts(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/high-risk-review",
            "target_ref": "main",
            "title": "security migration review",
            "changed_files": [
                "backend/db/migrations/20260312_add_payment_table.sql",
                "backend/app/security/authz.py",
            ],
            }
        )
    issue = _seed_pending_human_issue(service, review.review_id)

    service.record_human_decision(
        review.review_id,
        issue.issue_id,
        "approved",
        "人工确认需要整改",
    )
    refreshed_review = service.get_review(review.review_id)
    artifact_dir = storage_root / "reviews" / review.review_id / "artifacts"
    summary_comment = read_json(artifact_dir / "summary_comment.json")
    check_run = read_json(artifact_dir / "check_run.json")

    assert refreshed_review is not None
    assert "0 个待人工裁决" in refreshed_review.report_summary
    assert summary_comment["human_review_status"] == "approved"
    assert "0 个待人工裁决" in summary_comment["summary"]
    assert check_run["status"] == "completed"
    assert check_run["conclusion"] == "completed"


def test_human_decision_keeps_analysis_duration_ended_at_human_gate(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/high-risk-review",
            "target_ref": "main",
            "title": "security migration review",
            "changed_files": ["backend/app/security/authz.py"],
        }
    )
    issue = _seed_pending_human_issue(service, review.review_id)
    seeded = service.get_review(review.review_id)
    assert seeded is not None
    seeded.started_at = datetime(2026, 5, 27, 10, 0, tzinfo=UTC)
    seeded.completed_at = seeded.started_at + timedelta(seconds=42)
    seeded.duration_seconds = 42
    service.review_repo.save(seeded)

    updated = service.record_human_decision(
        review.review_id,
        issue.issue_id,
        "approved",
        "人工确认需要整改",
    )

    assert updated.status == "completed"
    assert updated.completed_at == seeded.completed_at
    assert updated.duration_seconds == 42


def test_waiting_human_review_duration_is_backfilled_from_human_gate_event(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/high-risk-review",
            "target_ref": "main",
            "title": "security migration review",
            "changed_files": ["backend/app/security/authz.py"],
        }
    )
    issue = _seed_pending_human_issue(service, review.review_id)
    started_at = datetime(2026, 5, 27, 10, 0, tzinfo=UTC)
    human_gate_at = started_at + timedelta(seconds=17)
    seeded = service.review_repo.get(review.review_id)
    assert seeded is not None
    seeded.started_at = started_at
    seeded.completed_at = None
    seeded.duration_seconds = None
    seeded.pending_human_issue_ids = [issue.issue_id]
    seeded.updated_at = started_at + timedelta(seconds=3)
    original_updated_at = seeded.updated_at
    service.review_repo.save(seeded)
    service.event_repo.append(
        ReviewEvent(
            review_id=review.review_id,
            event_type="human_gate_requested",
            phase="human_gate",
            message="高风险议题已提交人工复核",
            created_at=human_gate_at,
            payload={"issue_ids": [issue.issue_id]},
        )
    )

    hydrated = service.get_review(review.review_id)
    summaries = service.list_review_summaries()
    row = next(item for item in summaries if item["review_id"] == review.review_id)

    assert hydrated is not None
    assert hydrated.completed_at == human_gate_at
    assert hydrated.duration_seconds == 17
    assert hydrated.updated_at == original_updated_at
    assert datetime.fromisoformat(str(row["completed_at"]).replace("Z", "+00:00")) == human_gate_at
    assert row["duration_seconds"] == 17
    assert datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")) == original_updated_at


def test_human_decision_can_continue_with_remaining_pending_issues(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/multi-human-gate",
            "target_ref": "main",
            "title": "multi pending human review",
            "changed_files": ["backend/app/security/authz.py"],
        }
    )
    pending_a = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_human_a",
        title="高风险权限问题 A",
        summary="需要人工确认 A。",
        file_path="backend/app/security/authz.py",
        line_start=12,
        status="needs_human",
        severity="high",
        confidence=0.9,
        finding_ids=["fdg_a"],
        participant_expert_ids=["security_compliance"],
        needs_human=True,
    )
    pending_b = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_human_b",
        title="高风险权限问题 B",
        summary="需要人工确认 B。",
        file_path="backend/app/security/authz.py",
        line_start=24,
        status="needs_human",
        severity="high",
        confidence=0.88,
        finding_ids=["fdg_b"],
        participant_expert_ids=["security_compliance"],
        needs_human=True,
    )
    service.issue_repo.save_all(review.review_id, [pending_a, pending_b])
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [pending_a.issue_id, pending_b.issue_id]
    service.review_repo.save(review)

    updated = service.record_human_decision(review.review_id, pending_a.issue_id, "approved", "先处理 A")

    assert updated.status == "waiting_human"
    assert updated.phase == "human_gate"
    assert updated.human_review_status == "requested"
    assert updated.pending_human_issue_ids == [pending_b.issue_id]

    refreshed_issues = service.list_issues(review.review_id)
    issue_a = next(item for item in refreshed_issues if item.issue_id == pending_a.issue_id)
    issue_b = next(item for item in refreshed_issues if item.issue_id == pending_b.issue_id)
    assert issue_a.status == "resolved"
    assert issue_b.status == "needs_human"


def test_rehydrated_issue_preserves_canonical_issue_id_for_human_decision(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/rehydrated-human-gate",
            "target_ref": "main",
            "title": "rehydrated issue keeps canonical id",
            "changed_files": ["backend/app/security/authz.py"],
        }
    )
    persisted_issue = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_persisted_human_gate",
        title="同一代码点的多视角问题",
        summary="需要人工确认。",
        file_path="backend/app/security/authz.py",
        line_start=12,
        status="needs_human",
        severity="high",
        confidence=0.91,
        finding_ids=["fdg_a", "fdg_b"],
        participant_expert_ids=["security_compliance", "correctness_business"],
        needs_human=True,
    )
    service.issue_repo.save_all(review.review_id, [persisted_issue])
    service.finding_repo.save_many(
        review.review_id,
        [
            ReviewFinding(
                review_id=review.review_id,
                finding_id="fdg_a",
                expert_id="security_compliance",
                title="问题 A",
                summary="问题 A",
                file_path="backend/app/security/authz.py",
                line_start=12,
            ),
            ReviewFinding(
                review_id=review.review_id,
                finding_id="fdg_b",
                expert_id="correctness_business",
                title="问题 B",
                summary="问题 B",
                file_path="backend/app/security/authz.py",
                line_start=12,
            ),
        ],
    )
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [persisted_issue.issue_id]
    service.review_repo.save(review)

    listed_issues = service.list_issues(review.review_id)

    assert len(listed_issues) == 1
    assert listed_issues[0].issue_id == persisted_issue.issue_id
    assert {item.canonical_issue_id for item in listed_issues} == {persisted_issue.issue_id}

    updated = service.record_human_decision(
        review.review_id,
        listed_issues[0].canonical_issue_id,
        "approved",
        "人工确认问题成立",
    )

    assert updated.status == "completed"
