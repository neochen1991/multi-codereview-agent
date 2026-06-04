from app.domain.models.feedback import FeedbackLabel
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage
from app.services.review_service import ReviewService


def test_build_quality_metrics_calculates_static_tool_adoption_rates(storage_root):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_static_tools",
            "project_id": "proj",
            "source_ref": "feature/static-tools",
            "target_ref": "main",
            "title": "static tools",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_tool_verified",
                title="工具确认的 SQL 注入",
                summary="专家确认 semgrep 命中同一 SQL 拼接问题。",
                file_path="src/main/java/demo/UserDao.java",
                line_start=42,
                tool_verified=True,
                sast_cross_validated=True,
                sast_prescan_matches=[{"tool": "semgrep", "rule_id": "java.sql-injection"}],
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_sast_cross_validated",
                title="工具交叉验证的 token 泄露",
                summary="专家确认 SAST 命中同一 token 日志问题。",
                file_path="src/main/java/demo/UserController.java",
                line_start=20,
                sast_cross_validated=True,
                sast_prescan_matches=[{"tool": "semgrep", "rule_id": "token-log"}],
            ),
        ],
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="security_compliance",
            message_type="expert_tool_observation_scan",
            content="tool scan",
            metadata={
                "tool_observation_scan": {
                    "tool_observation_count": 4,
                    "candidate_count": 3,
                }
            },
        )
    )
    service.feedback_repo.save(
        FeedbackLabel(
            review_id=review.review_id,
            issue_id="iss_sast_cross_validated",
            label="false_positive",
            comment="人工判定该工具佐证的问题不成立。",
        )
    )

    metrics = service.build_quality_metrics()

    assert metrics["tool_observation_count"] == 4
    assert metrics["tool_adoption_rate"] == 0.75
    assert metrics["tool_confirmation_rate"] == 0.5
    assert metrics["sast_cross_validated_issue_count"] == 2
    assert metrics["tool_false_positive_rate"] == 0.25


def test_list_issues_projects_high_confidence_tool_backed_security_findings(storage_root):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_static_tool_issue_projection",
            "project_id": "proj",
            "source_ref": "feature/static-tool",
            "target_ref": "main",
            "title": "static tool issue projection",
            "changed_files": ["src/main/java/demo/UserDao.java"],
        }
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            expert_id="security_compliance",
            title="静态工具候选需复核：java.sql-injection",
            summary="semgrep 命中新增 SQL 字符串拼接，工具信号和当前 diff 指向同一注入风险。",
            normalized_issue_type="sql_injection_risk",
            severity="high",
            confidence=0.88,
            file_path="src/main/java/demo/UserDao.java",
            line_start=42,
            evidence=["User input is concatenated into SQL."],
            matched_rules=["java.sql-injection"],
            remediation_suggestion="改用 PreparedStatement 绑定参数，不要拼接用户输入。",
            code_context={
                "sast_fast_lane": True,
                "adopted_tool_observations": [
                    "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"
                ],
                "sast_cross_validated": True,
            },
        ),
    )

    issues = service.list_issues(review.review_id)

    assert [issue.title for issue in issues] == ["静态工具候选需复核：java.sql-injection"]
    assert issues[0].tool_verified is True
    assert issues[0].sast_cross_validated is True


def test_list_review_summaries_uses_display_issue_count_for_large_history(storage_root):
    service = ReviewService(storage_root=storage_root)
    target_review = None
    for index in range(4):
        review = service.create_review(
            {
                "subject_type": "mr",
                "repo_id": "repo_history_consistency",
                "project_id": "proj_history_consistency",
                "source_ref": f"feature/history-{index}",
                "target_ref": "main",
                "title": f"history consistency {index}",
            }
        )
        if index == 0:
            target_review = review
            service.issue_repo.save_all(
                review.review_id,
                [
                    DebateIssue(
                        review_id=review.review_id,
                        issue_id="iss_sql_security",
                        title="SQL 拼接风险",
                        summary="用户输入被拼接进 SQL。",
                        normalized_issue_type="sql_injection_risk",
                        status="open",
                        severity="high",
                        confidence=0.95,
                        file_path="src/main/java/demo/UserDao.java",
                        line_start=15,
                    ),
                    DebateIssue(
                        review_id=review.review_id,
                        issue_id="iss_sql_security_dup",
                        title="SQL 注入风险",
                        summary="同一行 SQL 拼接风险被另一个专家再次提出。",
                        normalized_issue_type="sql_injection_risk",
                        status="open",
                        severity="high",
                        confidence=0.9,
                        file_path="src/main/java/demo/UserDao.java",
                        line_start=15,
                    ),
                ],
            )

    assert target_review is not None

    summaries = service.list_review_summaries()
    row = next(item for item in summaries if item["review_id"] == target_review.review_id)
    result_page_count = len(service.list_display_issues(target_review.review_id))

    assert result_page_count == 1
    assert row["issue_count"] == result_page_count
    assert "形成 1 个有效问题" in row["report_summary"]
