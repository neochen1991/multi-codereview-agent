from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue


def _seed_export_issue(review_id: str) -> None:
    import app.services.review_service as review_service_module

    service = review_service_module.review_service
    finding = ReviewFinding(
        review_id=review_id,
        finding_id="fdg_export_seed",
        expert_id="correctness_business",
        title="订单创建失败路径未处理",
        summary="订单创建失败路径未处理。",
        file_path="backend/app/orders/service.py",
        line_start=12,
        suggested_code="if (!repository.save(order)) { throw new OrderCreateException(\"create failed\"); }",
    )
    issue = DebateIssue(
        review_id=review_id,
        issue_id="iss_export_seed",
        title=finding.title,
        summary=finding.summary,
        file_path=finding.file_path,
        line_start=finding.line_start,
        status="needs_human",
        severity="high",
        confidence=0.91,
        finding_ids=[finding.finding_id],
        participant_expert_ids=[finding.expert_id],
        needs_human=True,
        remediation_suggestion="补齐保存失败的错误处理，并用测试覆盖失败分支。",
        suggested_code=finding.suggested_code,
    )
    service.finding_repo.save_many(review_id, [finding])
    service.issue_repo.save_all(review_id, [issue])


def test_export_issues_to_codehub_mock_returns_selected_issue_payloads(client):
    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_export",
            "project_id": "proj_export",
            "source_ref": "feature/export-codehub",
            "target_ref": "main",
            "title": "export issues to codehub",
            "changed_files": [
                "backend/app/security/authz.py",
                "backend/app/orders/service.py",
            ],
        },
    ).json()

    client.post(f"/api/reviews/{created['review_id']}/start")
    _seed_export_issue(created["review_id"])

    issues_response = client.get(f"/api/reviews/{created['review_id']}/issues")
    findings_response = client.get(f"/api/reviews/{created['review_id']}/findings")
    assert issues_response.status_code == 200
    assert findings_response.status_code == 200

    issues = issues_response.json()
    findings = findings_response.json()
    assert issues
    assert findings

    target_issue = issues[0]
    response = client.post(
        f"/api/reviews/{created['review_id']}/issues/export/codehub",
        json={"issue_ids": [target_issue["issue_id"]]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_id"] == created["review_id"]
    assert payload["submitted_count"] == 1
    assert payload["status"] == "mock_submitted"
    assert len(payload["items"]) == 1

    exported = payload["items"][0]
    assert exported["issue_id"] == target_issue["issue_id"]
    assert exported["title"] == target_issue["title"]
    assert exported["problem_description"]
    assert exported["remediation_suggestion"]
    assert "mock://codehub/issues/" in exported["mock_ticket_url"]

    related_finding_ids = set(target_issue["finding_ids"])
    related_findings = [item for item in findings if item["finding_id"] in related_finding_ids]
    assert exported["patched_code"]
    assert any(item.get("suggested_code") == exported["patched_code"] for item in related_findings)


def test_export_issues_to_codehub_replaces_internal_fallback_text(client):
    import app.services.review_service as review_service_module

    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_export_sanitize",
            "project_id": "proj_export_sanitize",
            "source_ref": "feature/export-sanitize",
            "target_ref": "main",
            "title": "export sanitize",
            "changed_files": ["src/main/java/com/example/BulkEnrollmentService.java"],
        },
    ).json()
    service = review_service_module.review_service
    issue = DebateIssue(
        review_id=created["review_id"],
        issue_id="iss_export_sanitize",
        title="承诺未落地",
        summary="按当前代码片段定位具体分支并修复。",
        normalized_issue_type="comment_contract_unimplemented",
        file_path="src/main/java/com/example/BulkEnrollmentService.java",
        line_start=37,
        status="needs_human",
        severity="high",
        confidence=0.89,
        current_code=(
            "# src/main/java/com/example/BulkEnrollmentService.java\n"
            "  37 | +        // TODO 扣减库存并发送预占事件\n"
            "  38 | +        return result;\n"
        ),
        remediation_suggestion="按当前代码片段补齐缺失实现，并增加能复现该风险的回归测试。",
        remediation_steps=["识别批量输入规模", "回到当前代码锚点，补齐被规则命中的真实业务逻辑或保护逻辑。"],
        finding_ids=[],
        needs_human=True,
    )
    service.issue_repo.save_all(created["review_id"], [issue])

    response = client.post(
        f"/api/reviews/{created['review_id']}/issues/export/codehub",
        json={"issue_ids": [issue.issue_id]},
    )

    assert response.status_code == 200
    exported = response.json()["items"][0]
    exported_text = "\n".join(
        [
            exported["title"],
            exported["problem_description"],
            exported["remediation_suggestion"],
        ]
    )
    assert "当前代码锚点" not in exported_text
    assert "按当前代码片段" not in exported_text
    assert "识别批量输入规模" not in exported_text
    assert "BulkEnrollmentService.java 第 37 行 的注释或 TODO 已承诺业务动作" in exported["problem_description"]
    assert "补齐 BulkEnrollmentService.java 第 37 行 注释或 TODO 中承诺的业务动作" in exported["remediation_suggestion"]


def test_export_issues_to_codehub_uses_readable_exception_title(client):
    import app.services.review_service as review_service_module

    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_export_exception_title",
            "project_id": "proj_export_exception_title",
            "source_ref": "feature/export-exception-title",
            "target_ref": "main",
            "title": "export exception title",
            "changed_files": ["src/main/java/com/example/EventConsumer.java"],
        },
    ).json()
    service = review_service_module.review_service
    issue = DebateIssue(
        review_id=created["review_id"],
        issue_id="iss_export_exception_title",
        title="异常被吞掉",
        summary="EventConsumer 的 catch 分支忽略异常后继续按成功处理。",
        normalized_issue_type="exception_swallowed",
        file_path="src/main/java/com/example/EventConsumer.java",
        line_start=42,
        status="needs_human",
        severity="high",
        confidence=0.91,
        current_code=(
            "# src/main/java/com/example/EventConsumer.java\n"
            "  42 | +        } catch (RuntimeException ignored) {\n"
            "  43 | +            return success();\n"
        ),
        remediation_suggestion="不要吞掉异常，改为抛出业务异常或返回明确失败结果。",
        finding_ids=[],
        needs_human=True,
    )
    service.issue_repo.save_all(created["review_id"], [issue])

    response = client.post(
        f"/api/reviews/{created['review_id']}/issues/export/codehub",
        json={"issue_ids": [issue.issue_id]},
    )

    assert response.status_code == 200
    exported = response.json()["items"][0]
    assert exported["title"] == "失败被当成成功返回"
    assert "异常被吞掉" not in str(exported)


def test_export_issues_to_codehub_compacts_duplicate_remediation_text(client):
    import app.services.review_service as review_service_module

    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_export_compact",
            "project_id": "proj_export_compact",
            "source_ref": "feature/export-compact",
            "target_ref": "main",
            "title": "export compact",
            "changed_files": ["src/main/java/com/example/BulkEnrollmentService.java"],
        },
    ).json()
    service = review_service_module.review_service
    finding = ReviewFinding(
        review_id=created["review_id"],
        finding_id="fdg_export_compact",
        expert_id="performance_reliability",
        title="批量报名的锁保护被移除",
        summary="原有锁保护被删除，并发调用时缺少保护",
        normalized_issue_type="lock_guard_removed",
        file_path="src/main/java/com/example/BulkEnrollmentService.java",
        line_start=25,
        remediation_suggestion="恢复原有锁保护，或补上等价的幂等、唯一约束、分布式锁等等价并发控制。",
        remediation_steps=[
            "恢复原有锁保护或补上等价并发控制",
            "补充并发提交或重复消费场景的回归测试。",
        ],
    )
    issue = DebateIssue(
        review_id=created["review_id"],
        issue_id="iss_export_compact",
        title="批量报名的锁保护被移除",
        summary="BulkEnrollmentService 第 25 行移除了原有并发保护，并发调用时可能出现重复处理或状态竞争。",
        normalized_issue_type="lock_guard_removed",
        file_path=finding.file_path,
        line_start=finding.line_start,
        status="needs_human",
        severity="high",
        confidence=0.93,
        finding_ids=[finding.finding_id],
        participant_expert_ids=[finding.expert_id],
        remediation_suggestion="恢复原有锁保护，或补充数据库唯一约束、乐观锁、幂等表、分布式锁等等价并发控制。",
        remediation_steps=["恢复被删除的锁保护，或补充等价的幂等、唯一约束、分布式锁等并发控制。"],
        needs_human=True,
    )
    service.finding_repo.save_many(created["review_id"], [finding])
    service.issue_repo.save_all(created["review_id"], [issue])

    response = client.post(
        f"/api/reviews/{created['review_id']}/issues/export/codehub",
        json={"issue_ids": [issue.issue_id]},
    )

    assert response.status_code == 200
    exported = response.json()["items"][0]
    suggestion = exported["remediation_suggestion"]
    assert "等等价" not in suggestion
    assert suggestion.count("恢复") == 1
    assert "补充并发提交或重复消费场景的回归测试" in suggestion
    assert "其他专家补充：\n-" in exported["problem_description"]


def test_export_issues_to_codehub_suppresses_mismatched_patched_code(client):
    import app.services.review_service as review_service_module

    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_export_code_guard",
            "project_id": "proj_export_code_guard",
            "source_ref": "feature/export-code-guard",
            "target_ref": "main",
            "title": "export code guard",
            "changed_files": ["src/main/java/com/example/BulkEnrollmentService.java"],
        },
    ).json()
    service = review_service_module.review_service
    issue = DebateIssue(
        review_id=created["review_id"],
        issue_id="iss_export_code_guard",
        title="TODO 里的库存扣减未实现",
        summary="TODO 承诺扣减库存并发送预占事件，但当前实现没有对应代码。",
        normalized_issue_type="comment_contract_unimplemented",
        file_path="src/main/java/com/example/BulkEnrollmentService.java",
        line_start=37,
        status="needs_human",
        severity="high",
        confidence=0.9,
        current_code=(
            "# src/main/java/com/example/BulkEnrollmentService.java\n"
            "  37 | +        // TODO 扣减库存并发送预占事件\n"
            "  38 | +        eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));\n"
        ),
        suggested_code=(
            "List<CourseEnrollment> enrollments = studentIds.stream()\n"
            "    .map(studentId -> CourseEnrollment.create(courseId, studentId))\n"
            "    .toList();\n"
            "repository.saveAll(enrollments);\n"
            "eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));"
        ),
        finding_ids=[],
        needs_human=True,
    )
    service.issue_repo.save_all(created["review_id"], [issue])

    response = client.post(
        f"/api/reviews/{created['review_id']}/issues/export/codehub",
        json={"issue_ids": [issue.issue_id]},
    )

    assert response.status_code == 200
    exported = response.json()["items"][0]
    assert exported["patched_code"] == ""
    assert "补齐 BulkEnrollmentService.java 第 37 行 注释或 TODO 中承诺的业务动作" in exported["remediation_suggestion"]


def test_list_issues_sanitizes_expert_views_for_display(client):
    import app.services.review_service as review_service_module

    created = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_issue_display_sanitize",
            "project_id": "proj_issue_display_sanitize",
            "source_ref": "feature/issue-display-sanitize",
            "target_ref": "main",
            "title": "issue display sanitize",
            "changed_files": ["src/main/java/com/example/PaymentSettlementService.java"],
        },
    ).json()
    service = review_service_module.review_service
    issue = DebateIssue(
        review_id=created["review_id"],
        issue_id="iss_issue_display_sanitize",
        title="结算异常被静默吞掉后仍返回成功状态",
        summary="异常被静默吞掉后仍返回成功状态，无日志、无指标、无补偿动作",
        normalized_issue_type="exception_swallowed",
        file_path="src/main/java/com/example/PaymentSettlementService.java",
        line_start=30,
        status="needs_human",
        severity="high",
        confidence=0.92,
        current_code=(
            "# src/main/java/com/example/PaymentSettlementService.java\n"
            "  30 | +        } catch (RuntimeException ignored) {\n"
            "  31 | +            return SettlementResult.success(payments.size());\n"
        ),
        expert_views=[
                {
                    "expert_id": "correctness_business",
                    "title": "异常被静默吞掉后仍返回成功状态",
                    "summary": "当前异常路径把失败语义弱化成成功或兜底返回；需要对比原异常处理方式；当前变更在 PaymentSettlementService.java:30 存在异常被静默吞掉后仍返回成功状态，需要按该代码锚点单独修复。",
                }
            ],
        remediation_suggestion="按当前代码片段补齐缺失实现，并增加能复现该风险的回归测试。",
        finding_ids=[],
        consistency_check_summary="字段完整且代码锚点一致，已跳过 LLM 一致性校验。",
        needs_human=True,
    )
    loop_issue = DebateIssue(
        review_id=created["review_id"],
        issue_id="iss_issue_display_loop",
        title="批量结算从 saveAll 退化为循环逐条保存",
        summary="本次 diff 将原本的 paymentRepository.saveAll 改成循环内逐条 paymentRepository.save。",
        normalized_issue_type="n_plus_one",
        file_path="src/main/java/com/example/PaymentSettlementService.java",
        line_start=28,
        status="needs_human",
        severity="high",
        confidence=0.91,
        current_code=(
            "# src/main/java/com/example/PaymentSettlementService.java\n"
            "  28 | +                paymentRepository.save(payment);\n"
            "  29 | +            }\n"
        ),
        expert_views=[
            {
                "expert_id": "performance_reliability",
                "title": "循环内逐条外部调用会放大批量处理成本",
                "summary": "循环内调用 paymentRepository.save，批量输入会把数据库写入放大为 N 次。",
                "normalized_issue_type": "n_plus_one",
            },
            {
                "expert_id": "ddd_architecture",
                "title": "异常返回语义被弱化",
                "summary": "当前异常路径把失败语义弱化成成功或成功返回（catch / SettlementResult.success）。",
                "normalized_issue_type": "exception_swallowed",
            },
        ],
        finding_ids=[],
        needs_human=True,
    )
    comment_issue = DebateIssue(
        review_id=created["review_id"],
        issue_id="iss_issue_display_comment",
        title="TODO 里的库存扣减未实现",
        summary="TODO 承诺扣减库存并发送预占事件，但当前实现没有对应代码。",
        normalized_issue_type="comment_contract_unimplemented",
        file_path="src/main/java/com/example/BulkEnrollmentService.java",
        line_start=37,
        status="needs_human",
        severity="high",
        confidence=0.9,
        current_code=(
            "# src/main/java/com/example/BulkEnrollmentService.java\n"
            "  37 | +        // TODO 扣减库存并发送预占事件\n"
        ),
        expert_views=[
            {
                "expert_id": "correctness_business",
                "title": "承诺未落地",
                "summary": "TODO 已承诺扣减库存并发送预占事件，但当前代码没有对应实现。",
                "normalized_issue_type": "comment_contract_unimplemented",
            },
            {
                "expert_id": "ddd_architecture",
                "title": "跨聚合副作用未建模导致一致性边界缺失",
                "summary": "跨聚合副作用（报名→库存、支付→持久化）未通过 DomainEvent/Saga 建模，异常处理被忽略。",
                "normalized_issue_type": "comment_contract_unimplemented",
            },
        ],
        finding_ids=[],
        needs_human=True,
    )
    service.issue_repo.save_all(created["review_id"], [issue, loop_issue, comment_issue])

    response = client.get(f"/api/reviews/{created['review_id']}/issues")

    assert response.status_code == 200
    issues = {item["issue_id"]: item for item in response.json()}
    payload = issues["iss_issue_display_sanitize"]
    display_text = str(payload)
    assert "静默吞" not in display_text
    assert "静默忽略" not in display_text
    assert "兜底返回" not in display_text
    assert "需要对比" not in display_text
    assert "当前变更在" not in display_text
    assert "代码锚点" not in display_text
    assert payload["title"] == "支付结算失败后仍返回成功"
    assert "忽略异常并返回 SettlementResult.success" in payload["summary"]
    assert "代码位置一致" in payload["consistency_check_summary"]

    loop_payload = issues["iss_issue_display_loop"]
    loop_text = str(loop_payload)
    assert "异常返回语义" not in loop_text
    assert "SettlementResult.success" not in loop_text
    assert loop_payload["expert_views"] == [
        {
            "expert_id": "performance_reliability",
            "title": "循环内逐条外部调用会放大批量处理成本",
            "summary": "循环内调用 paymentRepository.save，批量输入会把数据库写入放大为 N 次。",
            "normalized_issue_type": "n_plus_one",
        }
    ]

    comment_payload = issues["iss_issue_display_comment"]
    assert "异常处理被忽略" not in str(comment_payload["expert_views"])
    assert comment_payload["expert_views"] == [
        {
            "expert_id": "correctness_business",
            "title": "承诺未落地",
            "summary": "TODO 已承诺扣减库存并发送预占事件，但当前代码没有对应实现。",
            "normalized_issue_type": "comment_contract_unimplemented",
        }
    ]
