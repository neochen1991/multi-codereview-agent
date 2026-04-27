from pathlib import Path

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage
from app.services.review_service import ReviewService


def test_start_review_keeps_weak_fallback_risks_as_findings(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/db-locking",
            "target_ref": "main",
            "title": "migration risk",
            "changed_files": [
                "backend/db/migrations/20260312_add_index.sql",
                "backend/app/repositories/order_repository.py",
            ],
        }
    )

    updated = service.start_review(review.review_id)
    issues = service.list_issues(review.review_id)

    findings = service.list_findings(review.review_id)

    assert updated.human_review_status == "not_required"
    assert findings
    assert issues == []


def test_list_issues_realigns_issue_location_from_linked_finding(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_2",
            "project_id": "proj_2",
            "source_ref": "feature/location-fix",
            "target_ref": "main",
            "title": "issue location fix",
        }
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_location_1",
        expert_id="correctness_business",
        title="真实问题行号",
        summary="问题实际发生在 88 行。",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=88,
    )
    service.finding_repo.save(review.review_id, finding)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_location_1",
                title="错误 issue 行号",
                summary="issue 持久化里带了错误行号。",
                file_path="src/main/java/com/example/Wrong.java",
                line_start=3,
                finding_ids=["fdg_location_1"],
            )
        ],
    )

    issues = service.list_issues(review.review_id)
    report = service.build_report(review.review_id)

    assert issues[0].file_path == "src/main/java/com/example/OrderService.java"
    assert issues[0].line_start == 88
    assert report.issues[0].file_path == "src/main/java/com/example/OrderService.java"
    assert report.issues[0].line_start == 88


def test_build_report_exposes_impact_report_for_each_review(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_impact",
            "project_id": "proj_impact",
            "source_ref": "feature/order-impact",
            "target_ref": "main",
            "title": "order impact",
            "changed_files": ["order-service/src/main/java/com/example/OrderController.java"],
            "unified_diff": (
                "diff --git a/order-service/src/main/java/com/example/OrderController.java "
                "b/order-service/src/main/java/com/example/OrderController.java\n"
                "@@ -10,2 +10,5 @@\n"
                "+public OrderDTO createOrder(CreateOrderRequest request) {\n"
                "+    return orderService.create(request);\n"
                "+}\n"
            ),
        }
    )

    report = service.build_report(review.review_id)

    assert report.impact_report is not None
    assert report.impact_report.graph_status == "fallback"
    assert report.impact_report.changed_files == ["order-service/src/main/java/com/example/OrderController.java"]
    assert report.impact_report.recommended_test_scope
    assert any("接口" in item.scope for item in report.impact_report.recommended_test_scope)


def test_list_issues_rehydrates_legacy_merged_issue_into_individual_findings(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_3",
            "project_id": "proj_3",
            "source_ref": "feature/legacy-merged-issue",
            "target_ref": "main",
            "title": "legacy merged issue",
        }
    )
    finding_a = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_split_a",
        expert_id="performance_reliability",
        title="循环内查库",
        summary="第 40 行在循环内部发起查询。",
        file_path="src/main/java/com/example/BatchJob.java",
        line_start=40,
        remediation_suggestion="把查询移出循环并做批量预取。",
    )
    finding_b = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_split_b",
        expert_id="correctness_business",
        title="异常被吞掉",
        summary="第 56 行 catch 后未处理异常。",
        file_path="src/main/java/com/example/BatchJob.java",
        line_start=56,
        remediation_suggestion="记录异常并返回受控失败。",
    )
    service.finding_repo.save(review.review_id, finding_a)
    service.finding_repo.save(review.review_id, finding_b)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_legacy_merged",
                title="同一代码行存在 2 个问题：循环内查库",
                summary="旧版合并 issue。",
                file_path="src/main/java/com/example/BatchJob.java",
                line_start=40,
                finding_ids=["fdg_split_a", "fdg_split_b"],
                aggregated_titles=["循环内查库", "异常被吞掉"],
            )
        ],
    )

    issues = service.list_issues(review.review_id)
    report = service.build_report(review.review_id)

    assert [item.issue_id for item in issues] == ["fdg_split_a", "fdg_split_b"]
    assert [item.title for item in issues] == ["循环内查库", "异常被吞掉"]
    assert [item.line_start for item in issues] == [40, 56]
    assert issues[0].finding_ids == ["fdg_split_a"]
    assert issues[1].finding_ids == ["fdg_split_b"]
    assert report.issue_count == 2
    assert [item.issue_id for item in report.issues] == ["fdg_split_a", "fdg_split_b"]


def test_list_issues_keeps_same_line_multi_expert_issue_merged(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_merged",
            "project_id": "proj_merged",
            "source_ref": "feature/same-root-cause",
            "target_ref": "main",
            "title": "same root cause issue",
        }
    )
    finding_a = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_query_a",
        expert_id="correctness_business",
        title="equals 被改成 like",
        summary="查询语义从精确匹配退化为模糊匹配。",
        file_path="src/shared/HibernateCriteriaConverter.java",
        line_start=63,
    )
    finding_b = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_query_b",
        expert_id="database_analysis",
        title="查询语义退化导致索引失效",
        summary="同一行 builder.like 破坏精确查询语义。",
        file_path="src/shared/HibernateCriteriaConverter.java",
        line_start=63,
    )
    service.finding_repo.save(review.review_id, finding_a)
    service.finding_repo.save(review.review_id, finding_b)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_query_merged",
                title="同一根因涉及 2 个专家发现：equals 被改成 like",
                summary="同一个查询语义退化问题。",
                file_path="src/shared/HibernateCriteriaConverter.java",
                line_start=63,
                finding_ids=["fdg_query_a", "fdg_query_b"],
                participant_expert_ids=["correctness_business", "database_analysis"],
                aggregated_titles=["equals 被改成 like", "查询语义退化导致索引失效"],
            )
        ],
    )

    issues = service.list_issues(review.review_id)
    report = service.build_report(review.review_id)

    assert len(issues) == 1
    assert issues[0].issue_id == "iss_query_merged"
    assert issues[0].finding_ids == ["fdg_query_a", "fdg_query_b"]
    assert report.issue_count == 1
    assert report.issues[0].issue_id == "iss_query_merged"


def test_list_issues_normalizes_query_semantics_family_for_report(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_query",
            "project_id": "proj_query",
            "source_ref": "feature/query-title",
            "target_ref": "main",
            "title": "query title",
        }
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_query_title",
        expert_id="architecture_design",
        title="方法名与实现语义严重不一致",
        summary="equalsPredicateTransformer 返回 builder.like。",
        file_path="src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        line_start=63,
    )
    service.finding_repo.save(review.review_id, finding)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_query_title",
                title="同一代码行存在 3 个问题：equals操作被错误替换为模糊like",
                summary="equals 查询语义从精确匹配退化为模糊匹配。",
                normalized_issue_type="magic_value_overuse",
                file_path=finding.file_path,
                line_start=63,
                finding_ids=["fdg_query_title"],
            )
        ],
    )

    issue = service.list_issues(review.review_id)[0]
    report_issue = service.build_report(review.review_id).issues[0]

    assert issue.normalized_issue_type == "query_semantics_regression"
    assert issue.title == "查询语义从精确匹配退化为模糊匹配"
    assert report_issue.normalized_issue_type == "query_semantics_regression"
    assert report_issue.title == "查询语义从精确匹配退化为模糊匹配"


def test_list_issues_normalizes_comment_contract_family_for_report(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_comment",
            "project_id": "proj_comment",
            "source_ref": "feature/comment-title",
            "target_ref": "main",
            "title": "comment title",
        }
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_comment_title",
        expert_id="correctness_business",
        title="承诺未落地",
        summary="TODO 承诺的审计事件没有真正实现。",
        file_path="src/main/java/com/example/CourseCreator.java",
        line_start=13,
    )
    service.finding_repo.save(review.review_id, finding)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_comment_title",
                title="CourseCreator 语义问题",
                summary="TODO 承诺的审计事件没有真正实现。",
                normalized_issue_type="course_creation_semantics",
                file_path=finding.file_path,
                line_start=13,
                finding_ids=["fdg_comment_title"],
            )
        ],
    )

    issue = service.list_issues(review.review_id)[0]
    report_issue = service.build_report(review.review_id).issues[0]

    assert issue.normalized_issue_type == "comment_contract_unimplemented"
    assert issue.title == "承诺未落地"
    assert report_issue.normalized_issue_type == "comment_contract_unimplemented"
    assert report_issue.title == "承诺未落地"


def test_build_report_exposes_llm_judge_stats(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_4",
            "project_id": "proj_4",
            "source_ref": "feature/llm-judge",
            "target_ref": "main",
            "title": "llm judge stats",
        }
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_llm_judge_1",
        expert_id="correctness_business",
        title="跨文件契约可能未同步",
        summary="方法签名变更后调用点可能仍沿用旧形态。",
        finding_type="risk_hypothesis",
        file_path="src/main/java/com/example/OwnerRepository.java",
        line_start=10,
    )
    service.finding_repo.save(review.review_id, finding)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_llm_judge_1",
                title="跨文件契约可能未同步",
                summary="签名变化需要继续验证。",
                finding_type="risk_hypothesis",
                file_path="src/main/java/com/example/OwnerRepository.java",
                line_start=10,
                finding_ids=["fdg_llm_judge_1"],
                llm_judge_result={
                    "final_verdict": "needs_verification",
                    "trigger_reason": "low_confidence<=0.78,cross_file_contract",
                    "reason": "跨文件证据存在，但调用方上下文还不完整",
                },
            )
        ],
    )

    report = service.build_report(review.review_id)

    assert report.issues[0].llm_judge_result["final_verdict"] == "needs_verification"
    assert report.confidence_summary.llm_judged_issue_count == 1
    assert report.confidence_summary.llm_judge_needs_verification_count == 1
    assert report.confidence_summary.llm_judge_accepted_count == 0


def test_build_report_exposes_llm_judge_rejected_filter_stats(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_5",
            "project_id": "proj_5",
            "source_ref": "feature/llm-judge-reject",
            "target_ref": "main",
            "title": "llm judge rejected stats",
        }
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="main_agent",
            message_type="issue_filter_applied",
            content="LLM Judge filtered issue",
            metadata={
                "phase": "coordination",
                "issue_filter_decisions": [
                    {
                        "topic": "iss_rejected",
                        "rule_code": "llm_judge_rejected",
                        "rule_label": "LLM Judge 拒绝",
                        "reason": "证据不足，结论依赖推测",
                        "severity": "medium",
                        "finding_ids": ["fdg_rejected"],
                        "finding_titles": ["需要确认是否有并发问题"],
                        "expert_ids": ["correctness_business"],
                    }
                ]
            },
        )
    )

    report = service.build_report(review.review_id)

    assert report.confidence_summary.llm_judge_rejected_count == 1
    assert report.confidence_summary.quality_filtered_issue_count == 1
