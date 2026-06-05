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
            message_type="sast_prescan_summary",
            content="sast summary",
            metadata={
                "finding_count": 6,
                "tool_observation_count": 4,
                "scanner_runs": [
                    {"tool": "semgrep", "finding_count": 5},
                    {"tool": "pmd", "finding_count": 1},
                ],
                "observations": [
                    {
                        "tool": "semgrep",
                        "rule_id": "java.sql-injection",
                        "file_path": "src/main/java/demo/UserDao.java",
                        "line_start": 42,
                    },
                    {
                        "tool": "semgrep",
                        "rule_id": "token-log",
                        "file_path": "src/main/java/demo/UserController.java",
                        "line_start": 20,
                    },
                    {
                        "tool": "pmd",
                        "rule_id": "EmptyCatchBlock",
                        "file_path": "src/main/java/demo/UserDao.java",
                        "line_start": 51,
                    },
                    {
                        "tool": "semgrep",
                        "rule_id": "unused-dangerous-html",
                        "file_path": "frontend/src/App.tsx",
                        "line_start": 12,
                    },
                ],
            },
        )
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
    assert metrics["tool_raw_signal_count"] == 6
    assert metrics["tool_diff_candidate_count"] == 4
    assert metrics["tool_expert_adopted_count"] == 3
    assert metrics["tool_formal_issue_count"] == 2
    assert metrics["tool_funnel"] == {
        "raw_signal_count": 6,
        "diff_candidate_count": 4,
        "expert_adopted_count": 3,
        "formal_issue_count": 2,
    }
    assert metrics["tool_confirmation_rate"] == 0.5
    assert metrics["sast_cross_validated_issue_count"] == 2
    assert metrics["tool_false_positive_rate"] == 0.25
    semgrep_row = next(item for item in metrics["tool_breakdown"] if item["tool"] == "semgrep")
    assert semgrep_row["raw_signal_count"] == 5
    assert semgrep_row["diff_candidate_count"] == 3
    assert semgrep_row["formal_issue_count"] == 2
    assert semgrep_row["false_positive_count"] == 1
    token_rule_row = next(item for item in metrics["rule_breakdown"] if item["rule_key"] == "semgrep:token-log")
    assert token_rule_row["formal_issue_count"] == 1
    assert token_rule_row["false_positive_rate"] == 1.0
    expert_row = next(item for item in metrics["expert_tool_breakdown"] if item["expert_id"] == "security_compliance")
    assert expert_row["diff_candidate_count"] == 4
    assert expert_row["expert_adopted_count"] == 3


def test_list_issues_keeps_fast_lane_tool_findings_out_of_display_issues(storage_root):
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
                "adopted_tool_observations": [
                    "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"
                ],
                "sast_cross_validated": True,
            },
        ),
    )

    issues = service.list_issues(review.review_id)

    assert issues == []


def test_report_issue_recovers_sast_match_from_dialogue_when_finding_context_is_lost(storage_root):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_report_sast_recovery",
            "project_id": "proj",
            "source_ref": "feature/empty-catch",
            "target_ref": "main",
            "title": "report sast recovery",
            "changed_files": ["src/main/java/demo/UserDao.java"],
            "unified_diff": (
                "diff --git a/src/main/java/demo/UserDao.java b/src/main/java/demo/UserDao.java\n"
                "--- a/src/main/java/demo/UserDao.java\n"
                "+++ b/src/main/java/demo/UserDao.java\n"
                "@@ -4,4 +4,7 @@\n"
                "+    void sync() {\n"
                "+        try {\n"
                "+            risky();\n"
                "+        } catch (Exception ex) {\n"
                "+        }\n"
                "+    }\n"
            ),
        }
    )
    review.status = "completed"
    review.phase = "completed"
    service.review_repo.save(review)
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            expert_id="correctness_business",
            title="异常被忽略后仍继续成功路径",
            summary="UserDao.java 第 7 行的 catch 分支忽略异常。",
            finding_type="direct_defect",
            normalized_issue_type="exception_swallowed",
            severity="high",
            confidence=0.9,
            file_path="src/main/java/demo/UserDao.java",
            line_start=7,
            evidence=["catch", "throw", "pmd:EmptyCatchBlock"],
            matched_rules=["CODE-JAVA-002", "pmd:EmptyCatchBlock"],
            remediation_suggestion="不要保留空 catch。",
            code_excerpt=(
                "# src/main/java/demo/UserDao.java\n"
                "   5 | +        try {\n"
                "   6 | +            risky();\n"
                "   7 | +        } catch (Exception ex) {\n"
                "   8 | +        }"
            ),
            code_context={},
        ),
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="main_agent",
            message_type="sast_prescan_summary",
            content="SAST summary",
            metadata={
                "observations": [
                    {
                        "tool": "pmd",
                        "rule_id": "EmptyCatchBlock",
                        "file_path": "src/main/java/demo/UserDao.java",
                        "line_start": 7,
                        "message": "Avoid empty catch blocks",
                        "observation_id": "sast:pmd:EmptyCatchBlock:src/main/java/demo/UserDao.java:7",
                    }
                ]
            },
        )
    )

    finding = service.finding_repo.list(review.review_id)[0]
    issue = service._build_issue_from_finding(review.review_id, finding, None)

    assert issue.sast_cross_validated is True
    assert issue.tool_name == "pmd"
    assert issue.tool_verified is True
    assert issue.sast_prescan_matches[0]["rule_id"] == "EmptyCatchBlock"


def test_process_and_replay_messages_preserve_sast_metadata(storage_root):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_sast_projection",
            "project_id": "proj",
            "source_ref": "feature/sast",
            "target_ref": "main",
            "title": "sast projection",
        }
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="main_agent",
            message_type="sast_prescan_summary",
            content="SAST summary",
            metadata={
                "phase": "sast_prescan",
                "tool_name": "sast_prescan",
                "scan_count": 1,
                "enabled_scan_count": 1,
                "finding_count": 2,
                "tool_observation_count": 1,
                "scanner_runs": [
                    {
                        "tool": "pmd",
                        "status": "completed",
                        "finding_count": 1,
                        "duration_ms": 120,
                        "used_project_config": True,
                        "config_path": "/repo/pmd-ruleset.xml",
                    }
                ],
                "scanner_status_counts": {"completed": 1},
                "config_files": ["/repo/pmd-ruleset.xml"],
                "observations": [
                    {
                        "tool": "pmd",
                        "rule_id": "EmptyCatchBlock",
                        "file_path": "src/main/java/demo/UserDao.java",
                        "line_start": 7,
                    }
                ],
                "observation_ids": [
                    "sast:pmd:EmptyCatchBlock:src/main/java/demo/UserDao.java:7"
                ],
            },
        )
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="main_agent",
            message_type="sast_candidate_report",
            content="candidate report",
            metadata={
                "phase": "sast_prescan",
                "tool_name": "sast_prescan",
                "candidate_count": 1,
                "expert_confirmed": False,
                "counts_as_formal_issue": False,
                "by_tool": {"pmd": 1},
                "observation_ids": [
                    "sast:pmd:EmptyCatchBlock:src/main/java/demo/UserDao.java:7"
                ],
            },
        )
    )

    process_messages = service.build_process_messages(review.review_id)
    replay_messages = service.build_replay_bundle(review.review_id)["messages"]

    process_summary = next(item for item in process_messages if item["message_type"] == "sast_prescan_summary")
    process_candidate = next(item for item in process_messages if item["message_type"] == "sast_candidate_report")
    replay_summary = next(item for item in replay_messages if item["message_type"] == "sast_prescan_summary")
    replay_candidate = next(item for item in replay_messages if item["message_type"] == "sast_candidate_report")

    assert process_summary["metadata"]["scanner_runs"][0]["tool"] == "pmd"
    assert process_summary["metadata"]["tool_observation_count"] == 1
    assert process_candidate["metadata"]["candidate_count"] == 1
    assert process_candidate["metadata"]["counts_as_formal_issue"] is False
    assert replay_summary["metadata"]["scanner_status_counts"] == {"completed": 1}
    assert replay_candidate["metadata"]["by_tool"] == {"pmd": 1}


def test_build_report_attaches_unpromoted_reason_to_each_filtered_finding(storage_root):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_unpromoted_reason",
            "project_id": "proj",
            "source_ref": "feature/unpromoted-reason",
            "target_ref": "main",
            "title": "unpromoted finding reason",
        }
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_low_confidence",
            expert_id="database_analysis",
            title="错误码分支缺少覆盖",
            summary="新增错误码分支没有测试覆盖，回归时可能漏掉异常路径。",
            finding_type="direct_defect",
            normalized_issue_type="error_branch_missing_test",
            severity="medium",
            confidence=0.61,
            file_path="src/main/java/demo/UserService.java",
            line_start=42,
            code_excerpt="42 | +        return Result.failed(ERROR_CODE);",
        ),
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="main_agent",
            message_type="issue_filter_applied",
            content="finding kept as observation",
            metadata={
                "issue_filter_decisions": [
                    {
                        "topic": "src/main/java/demo/UserService.java::42::fdg_low_confidence",
                        "rule_code": "below_priority_confidence_threshold",
                        "rule_label": "低于当前 P 级 issue 置信度阈值",
                        "reason": "当前 finding 达到 P2，但置信度 0.61 低于该级别配置的 issue 置信度阈值 0.80，因此仅保留为 finding。",
                        "severity": "medium",
                        "finding_ids": ["fdg_low_confidence"],
                        "finding_titles": ["错误码分支缺少覆盖"],
                        "expert_ids": ["database_analysis"],
                    }
                ]
            },
        )
    )

    report = service.build_report(review.review_id)

    decision = report.findings[0].code_context.get("unpromoted_decision")
    assert isinstance(decision, dict)
    assert decision["rule_code"] == "below_priority_confidence_threshold"
    assert "置信度 0.61" in str(decision["reason"])


def test_pending_human_exception_issue_survives_when_code_excerpt_is_truncated(storage_root):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_pending_exception_projection",
            "project_id": "proj",
            "source_ref": "feature/exception",
            "target_ref": "main",
            "title": "pending exception projection",
            "changed_files": ["src/main/java/demo/OrderWorkflow.java"],
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_exception_pending",
                title="失败被当成成功返回",
                summary="catch 分支吞掉 RuntimeException，并在失败路径返回成功结果，调用方会误以为处理已经完成。",
                normalized_issue_type="exception_swallowed",
                file_path="src/main/java/demo/OrderWorkflow.java",
                line_start=7,
                current_code="try {\n    reservePayment(order);\n}",
                remediation_suggestion="不要在 catch 分支返回 success；应抛出异常或返回明确失败结果。",
                evidence=["catch (RuntimeException ignored)", "return Result.success()"],
                status="needs_human",
                resolution="needs_human_review",
                consistency_check_status="repaired",
                needs_human=True,
            )
        ],
    )
    review.status = "waiting_human"
    review.phase = "human_gate"
    review.pending_human_issue_ids = ["iss_exception_pending"]
    service.review_repo.save(review)

    issues = service.list_issues(review.review_id)
    detail = service.build_review_display_payload(review.review_id)

    assert [issue.issue_id for issue in issues] == ["iss_exception_pending"]
    assert issues[0].needs_human is True
    assert detail["issue_count"] == 1


def test_report_keeps_distinct_security_families_on_same_line(storage_root):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_security_family_projection",
            "project_id": "proj",
            "source_ref": "feature/security",
            "target_ref": "main",
            "title": "security family projection",
            "changed_files": ["src/main/java/demo/UserController.java"],
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_secret_log",
                title="Authorization token 被写入日志",
                summary="logger.info 直接拼接 authorizationHeader，会把凭证写入日志。",
                normalized_issue_type="security_guard_removed",
                file_path="src/main/java/demo/UserController.java",
                line_start=9,
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_object_auth",
                title="用户资料接口缺少对象级授权校验",
                summary="profile 方法没有校验当前用户是否拥有对目标 username 资源的访问权限。",
                normalized_issue_type="security_guard_removed",
                file_path="src/main/java/demo/UserController.java",
                line_start=9,
            ),
        ],
    )

    issues = service.list_issues(review.review_id)
    report = service.build_report(review.review_id)

    assert {issue.issue_id for issue in issues} == {"iss_secret_log", "iss_object_auth"}
    assert {issue.issue_id for issue in report.issues} == {"iss_secret_log", "iss_object_auth"}


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
