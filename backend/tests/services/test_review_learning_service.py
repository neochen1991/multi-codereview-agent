from pathlib import Path

from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewSubject, ReviewTask
from app.services.review_learning_service import ReviewLearningService


def _review(repo_id: str = "repo_java") -> ReviewTask:
    return ReviewTask(
        review_id="rev_learning",
        status="waiting_human",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id=repo_id,
            project_id="proj_java",
            source_ref="feature/comment-contract",
            target_ref="dev",
            title="学习反馈样例",
            changed_files=["src/main/java/com/acme/order/OrderRepository.java"],
        ),
    )


def _comment_contract_issue() -> DebateIssue:
    return DebateIssue(
        review_id="rev_learning",
        issue_id="iss_contract",
        title="接口声明的方法未在实现类落地",
        summary="OrderRepository 接口定义了 findActive 方法，但实现类没有实际实现。",
        normalized_issue_type="comment_contract_unimplemented",
        file_path="src/main/java/com/acme/order/OrderRepository.java",
        line_start=18,
        primary_expert_id="correctness_business",
        participant_expert_ids=["correctness_business"],
        evidence=[
            "接口 OrderRepository 声明 findActive(Long userId)。",
            "Tree-sitter 上下文显示 JdbcOrderRepository implements OrderRepository。",
            "JdbcOrderRepository 中存在 @Override public List<Order> findActive(Long userId) { return jdbc.query(...); }。",
        ],
        cross_file_evidence=["src/main/java/com/acme/order/JdbcOrderRepository.java 已实现该接口方法。"],
        context_files=["src/main/java/com/acme/order/JdbcOrderRepository.java"],
        confidence=0.82,
    )


def test_review_learning_records_rejected_issue_as_case(storage_root: Path) -> None:
    service = ReviewLearningService(storage_root)

    saved = service.record_issue_decision_case(
        review=_review(),
        issue=_comment_contract_issue(),
        decision="rejected",
        comment="误报：实现类 JdbcOrderRepository 已 implements OrderRepository，并且 @Override 了 findActive。",
    )

    assert saved is not None
    assert saved["decision"] == "rejected"
    assert saved["repo_id"] == "repo_java"
    assert saved["issue_type"] == "comment_contract_unimplemented"
    assert saved["reason_category"] == "context_counterexample"
    assert "实现类已 implements" in saved["learning_summary"]

    cases = service.list_cases(repo_id="repo_java")
    assert [case["case_id"] for case in cases] == [saved["case_id"]]


def test_review_learning_records_approved_issue_as_confirmed_case(storage_root: Path) -> None:
    service = ReviewLearningService(storage_root)

    saved = service.record_issue_decision_case(
        review=_review(),
        issue=_comment_contract_issue().model_copy(
            update={
                "summary": "接口说明要求权限过滤，但实现类缺少权限条件。",
                "normalized_issue_type": "missing_auth_check",
                "evidence": [
                    "新增 queryActive 方法没有传入 userId 条件。",
                    "Controller 入口依赖当前用户过滤订单。",
                ],
                "context_files": ["src/main/java/com/acme/order/OrderController.java"],
            }
        ),
        decision="approved",
        comment="确认问题成立：缺少当前用户过滤会导致越权查询。",
    )

    assert saved is not None
    assert saved["decision"] == "approved"
    assert saved["reason_category"] == "confirmed_risk"
    assert "历史人工确认" in saved["learning_summary"]


def test_review_learning_builds_compact_prompt_hints(storage_root: Path) -> None:
    service = ReviewLearningService(storage_root)
    service.record_issue_decision_case(
        review=_review(),
        issue=_comment_contract_issue(),
        decision="rejected",
        comment="误报：实现类已经实现接口方法。",
    )

    hints = service.build_prompt_hints(
        repo_id="repo_java",
        issue_types=["comment_contract_unimplemented"],
        file_paths=["src/main/java/com/acme/order/OrderRepository.java"],
        max_items=3,
        max_chars=600,
    )

    assert "历史误报边界" in hints
    assert "comment_contract_unimplemented" in hints
    assert "不要报承诺未落地" in hints
    assert len(hints) <= 600


def test_review_learning_prompt_hints_do_not_use_same_repo_only_matches(storage_root: Path) -> None:
    service = ReviewLearningService(storage_root)
    service.record_issue_decision_case(
        review=_review(repo_id="repo_java"),
        issue=_comment_contract_issue(),
        decision="rejected",
        comment="误报：实现类已经实现接口方法。",
    )

    hints = service.build_prompt_hints(
        repo_id="repo_java",
        issue_types=[],
        file_paths=["src/main/java/com/acme/payment/PaymentService.java"],
        max_items=3,
        max_chars=600,
    )

    assert hints == ""


def test_review_learning_rejects_similar_comment_contract_false_positive(storage_root: Path) -> None:
    service = ReviewLearningService(storage_root)
    service.record_issue_decision_case(
        review=_review(),
        issue=_comment_contract_issue(),
        decision="rejected",
        comment="误报：实现类 JdbcOrderRepository 已 implements OrderRepository，并且 @Override 了 findActive。",
    )

    decision = service.evaluate_issue_candidate(
        repo_id="repo_java",
        issue={
            "issue_id": "iss_new",
            "title": "接口声明的方法未在实现类落地",
            "summary": "OrderRepository.findActive 没有实现。",
            "normalized_issue_type": "comment_contract_unimplemented",
            "file_path": "src/main/java/com/acme/order/OrderRepository.java",
            "line_start": 18,
            "claim": "接口承诺未落地。",
            "evidence": [
                "OrderRepository 声明 findActive(Long userId)。",
                "Tree-sitter 上下文显示 JdbcOrderRepository implements OrderRepository。",
                "JdbcOrderRepository 存在 @Override public List<Order> findActive(Long userId) { return jdbc.query(...); }。",
            ],
            "cross_file_evidence": ["JdbcOrderRepository 已实现 findActive。"],
            "context_files": ["src/main/java/com/acme/order/JdbcOrderRepository.java"],
            "confidence": 0.83,
        },
    )

    assert decision["action"] == "reject"
    assert decision["matched_case_id"]
    assert decision["similarity"] >= 0.85
    assert "历史人工驳回案例" in decision["reason"]


def test_review_learning_boosts_similar_approved_issue_and_records_match(storage_root: Path) -> None:
    service = ReviewLearningService(storage_root)
    saved = service.record_issue_decision_case(
        review=_review(),
        issue=_comment_contract_issue().model_copy(
            update={
                "summary": "接口说明要求权限过滤，但实现类缺少权限条件。",
                "normalized_issue_type": "missing_auth_check",
                "file_path": "src/main/java/com/acme/order/OrderRepository.java",
                "evidence": [
                    "新增 queryActive 方法没有传入 userId 条件。",
                    "Controller 入口依赖当前用户过滤订单。",
                ],
                "context_files": ["src/main/java/com/acme/order/OrderController.java"],
            }
        ),
        decision="approved",
        comment="确认问题成立：缺少当前用户过滤会导致越权查询。",
    )
    assert saved is not None

    decision = service.evaluate_issue_candidate(
        repo_id="repo_java",
        issue={
            "issue_id": "iss_auth_new",
            "title": "缺少当前用户过滤",
            "summary": "queryActive 没有按当前用户过滤。",
            "normalized_issue_type": "missing_auth_check",
            "file_path": "src/main/java/com/acme/order/OrderRepository.java",
            "claim": "新增查询缺少 userId 条件，可能越权读取订单。",
            "evidence": [
                "queryActive 方法没有传入 userId 条件。",
                "Controller 入口依赖当前用户过滤订单。",
            ],
            "context_files": ["src/main/java/com/acme/order/OrderController.java"],
        },
        record_match=True,
    )

    assert decision["action"] == "boost"
    assert decision["matched_case_id"] == saved["case_id"]
    assert decision["confidence_adjustment"] > 0
    refreshed = service.list_cases(repo_id="repo_java", issue_type="missing_auth_check")[0]
    assert refreshed["match_count"] == 1
    assert refreshed["last_matched_at"]
