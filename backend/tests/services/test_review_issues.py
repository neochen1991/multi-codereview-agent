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


def test_list_issues_normalizes_display_code_and_suggested_code(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_display",
            "project_id": "proj_display",
            "source_ref": "feature/display-fields",
            "target_ref": "main",
            "title": "display field normalization",
        }
    )
    current_code = (
        "# src/main/java/com/example/order/OrderService.java\n"
        "  18 |      public List<OrderDTO> listOrders(List<Long> orderIds) {\n"
        "  19 | +        // TODO 按产品要求，只能返回当前登录用户有权限的订单，避免越权读取\n"
        "  20 |          List<OrderDTO> result = new ArrayList<>();\n"
        "  21 |          for (Long orderId : orderIds) {\n"
        "  22 | +            // 每个订单循环查询一次数据库，批量场景会触发 N+1 查询\n"
        "  23 |              Order order = orderRepository.findById(orderId);\n"
        "  24 |              if (order != null) {\n"
        "  25 |                  result.add(toDTO(order));\n"
        "  26 |              }\n"
        "  31 | +    public void createOrder(OrderRequest request) {\n"
        "  32 | +        // 这里应该先校验库存并加库存锁，防止并发超卖\n"
        "  33 | +        Order order = new Order(request.getSkuId(), request.getQuantity());\n"
        "  34 | +        orderRepository.save(order);\n"
        "  35 | +        eventPublisher.publish(new OrderCreatedEvent(order.getId()));\n"
        "  36 | +    }"
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_display_stock",
                title="承诺未落地",
                summary="createOrder 的注释承诺先校验库存并加锁防止超卖，但当前实现直接创建订单、保存并发布事件，缺少并发库存保护。",
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/order/OrderService.java",
                line_start=19,
                current_code=current_code,
                suggested_code=(
                    "public void createOrder(OrderRequest request) {\n"
                    "    // 这里应该先校验库存并加库存锁，防止并发超卖\n"
                    "    Order order = new Order(request.getSkuId(), request.getQuantity());\n"
                    "    orderRepository.save(order);\n"
                    "}"
                ),
                remediation_suggestion="请根据规则要求补齐正确实现，并保留必要测试。",
            )
        ],
    )

    issue = service.list_issues(review.review_id)[0]

    assert issue.line_start == 32
    assert "createOrder" in issue.current_code
    assert "eventPublisher.publish" in issue.current_code
    assert "listOrders" not in issue.current_code
    assert issue.suggested_code == ""
    assert issue.remediation_suggestion == ""


def test_build_report_prefers_loop_call_display_over_incidental_contract_word(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_loop_display",
            "project_id": "proj_loop_display",
            "source_ref": "feature/bulk-loop",
            "target_ref": "main",
            "title": "loop display normalization",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_loop_display",
                title="承诺未落地",
                summary="",
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=31,
                participant_expert_ids=["performance_reliability"],
                primary_expert_id="performance_reliability",
                aggregated_titles=[
                    "批量报名从 saveAll 退化为循环逐条 save，会把批量写入放大为 N 次持久化调用（循环调用放大）",
                    "批量报名写入被改为逐条 repository.save，缺少批大小与事务范围控制（循环调用放大）",
                ],
                aggregated_summaries=[
                    "批量报名入口接收 List<StudentId>，当前改动把批量写入放大为逐条仓储写入，且没有控制 batch size 或事务范围；当前实现存在循环内调用放大（for (CourseEnrollment enrollment : enrollments) { / repository.save）。",
                ],
                current_code=(
                    "# src/main/java/com/example/BulkEnrollmentService.java\n"
                    "  34 | +        for (CourseEnrollment enrollment : enrollments) {\n"
                    "  35 | +            repository.save(enrollment);\n"
                    "  36 | +        }"
                ),
                suggested_code="repository.saveAll(enrollments);",
            )
        ],
    )

    report_issue = service.build_report(review.review_id).issues[0]

    assert report_issue.normalized_issue_type == "n_plus_one"
    assert report_issue.title == "批量报名从 saveAll 退化为循环逐条保存"
    assert "repository.save" in report_issue.summary
    assert "承诺未落地" not in report_issue.title
    assert "按当前代码片段" not in report_issue.remediation_suggestion
    assert "当前代码锚点" not in report_issue.remediation_suggestion
    assert "repository.save" in report_issue.remediation_suggestion


def test_list_issues_hides_mismatched_comment_contract_anchor(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_anchor_filter",
            "project_id": "proj_anchor_filter",
            "source_ref": "feature/mismatched-anchor",
            "target_ref": "main",
            "title": "mismatched issue anchor",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_wrong_todo_anchor",
                title="TODO 里的库存扣减未实现",
                summary="TODO 里的库存扣减未实现。",
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/CourseCreator.java",
                line_start=23,
                current_code=(
                    "# src/main/java/com/example/CourseCreator.java\n"
                    "  22 |          eventBus.publish(course.pullDomainEvents());\n"
                    "  23 | +        repository.save(course);"
                ),
                suggested_code="repository.save(course);\neventBus.publish(course.pullDomainEvents());",
                consistency_check_status="downgraded",
                resolution="consistency_validation_failed",
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_real_todo_anchor",
                title="TODO 里的库存扣减未实现",
                summary="BulkEnrollmentService 第 37 行的 TODO 承诺扣减库存并发送预占事件，但当前实现没有对应业务动作。",
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=37,
                current_code=(
                    "# src/main/java/com/example/BulkEnrollmentService.java\n"
                    "  37 | +        // TODO 批量报名成功后扣减库存并发送预占事件\n"
                    "  38 | +        eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));"
                ),
                suggested_code="inventoryReservationService.reserve(courseId, studentIds);\neventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));",
            ),
        ],
    )

    issues = service.list_issues(review.review_id)

    assert [issue.issue_id for issue in issues] == ["iss_real_todo_anchor"]
    assert issues[0].file_path.endswith("BulkEnrollmentService.java")


def test_list_issues_hides_same_current_and_suggested_code(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_same_code_filter",
            "project_id": "proj_same_code_filter",
            "source_ref": "feature/same-code",
            "target_ref": "main",
            "title": "same code issue filter",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_same_code",
                title="批量报名的锁保护被移除",
                summary="BulkEnrollmentService 第 25 行缺少原有 synchronized 锁保护。",
                normalized_issue_type="lock_guard_removed",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=25,
                current_code=(
                    "# src/main/java/com/example/BulkEnrollmentService.java\n"
                    "  25 | +        repository.saveAll(enrollments);"
                ),
                suggested_code="repository.saveAll(enrollments);",
            )
        ],
    )

    assert service.list_issues(review.review_id) == []


def test_list_issues_hides_loop_issue_when_current_code_has_no_loop(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_loop_anchor_filter",
            "project_id": "proj_loop_anchor_filter",
            "source_ref": "feature/loop-anchor",
            "target_ref": "main",
            "title": "loop anchor filter",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_wrong_loop_anchor",
                title="批量保存改成了循环逐条保存",
                summary="循环内调用 repository.save 会放大为 N 次数据库访问。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/CourseCreator.java",
                line_start=23,
                current_code=(
                    "# src/main/java/com/example/CourseCreator.java\n"
                    "  22 |          eventBus.publish(course.pullDomainEvents());\n"
                    "  23 | +        repository.save(course);"
                ),
                suggested_code="repository.save(course);\neventBus.publish(course.pullDomainEvents());",
                evidence=["for (CourseEnrollment enrollment : enrollments) {", "repository.save"],
                consistency_check_status="downgraded",
                resolution="consistency_validation_failed",
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_real_loop_anchor",
                title="批量报名从 saveAll 退化为循环逐条保存",
                summary="循环内逐条 repository.save 会把批量写入放大为 N 次。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=35,
                current_code=(
                    "# src/main/java/com/example/BulkEnrollmentService.java\n"
                    "  34 | +        for (CourseEnrollment enrollment : enrollments) {\n"
                    "  35 | +            repository.save(enrollment);\n"
                    "  36 | +        }"
                ),
                suggested_code="repository.saveAll(enrollments);",
            ),
        ],
    )

    issues = service.list_issues(review.review_id)

    assert [issue.issue_id for issue in issues] == ["iss_real_loop_anchor"]
    assert issues[0].file_path.endswith("BulkEnrollmentService.java")


def test_list_issues_hides_loop_issue_when_only_summary_mentions_loop(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_loop_summary_only",
            "project_id": "proj_loop_summary_only",
            "source_ref": "feature/loop-summary-only",
            "target_ref": "main",
            "title": "loop summary only",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_loop_summary_only",
                title="循环内逐条外部调用会放大批量处理成本",
                summary="本次 diff 在循环 `for (CourseEnrollment enrollment : enrollments)` 内调用 repository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/CourseCreator.java",
                line_start=23,
                current_code=(
                    "# src/main/java/com/example/CourseCreator.java\n"
                    "  22 |          eventBus.publish(course.pullDomainEvents());\n"
                    "  23 | +        repository.save(course);"
                ),
                suggested_code="repository.save(course);\neventBus.publish(course.pullDomainEvents());",
                confidence=0.92,
            )
        ],
    )

    assert service.list_issues(review.review_id) == []


def test_list_issues_rewrites_payment_loop_summary_from_anchor_code(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_payment_loop_rewrite",
            "project_id": "proj_payment_loop_rewrite",
            "source_ref": "feature/payment-loop",
            "target_ref": "main",
            "title": "payment loop rewrite",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_payment_loop",
                title="异常吞没：catch (RuntimeException ignored) 将失败伪装为成功（循环调用放大）",
                summary="PaymentSettlementService.java:30 定位到异常吞没，但 normalized_issue_type 是 n_plus_one。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/PaymentSettlementService.java",
                line_start=25,
                current_code=(
                    "# src/main/java/com/example/PaymentSettlementService.java\n"
                    "  25 | +            for (Payment payment : payments) {\n"
                    "  26 | +                gateway.capture(payment);\n"
                    "  27 | +                payment.markCaptured();"
                ),
                suggested_code=(
                    "for (Payment payment : payments) {\n"
                    "    gateway.capture(payment);\n"
                    "    payment.markCaptured();\n"
                    "}"
                ),
                participant_expert_ids=["performance_reliability"],
                confidence=0.86,
            )
        ],
    )

    issue = service.list_issues(review.review_id)[0]

    assert issue.normalized_issue_type == "n_plus_one"
    assert issue.title == "支付结算循环内逐条调用支付网关"
    assert "gateway.capture" in issue.summary
    assert "异常吞没" not in issue.summary


def test_list_issues_hydrates_truncated_loop_code_from_linked_finding(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_truncated_loop",
            "project_id": "proj_truncated_loop",
            "source_ref": "feature/truncated-loop",
            "target_ref": "main",
            "title": "truncated loop",
            "changed_files": ["src/main/java/com/example/BulkEnrollmentService.java"],
            "unified_diff": (
                "diff --git a/src/main/java/com/example/BulkEnrollmentService.java b/src/main/java/com/example/BulkEnrollmentService.java\n"
                "--- a/src/main/java/com/example/BulkEnrollmentService.java\n"
                "+++ b/src/main/java/com/example/BulkEnrollmentService.java\n"
                "@@ -31,4 +31,7 @@\n"
                "+        List<CourseEnrollment> enrollments = studentIds.stream()\n"
                "+            .map(studentId -> CourseEnrollment.create(courseId, studentId))\n"
                "+            .toList();\n"
                "+        for (CourseEnrollment enrollment : enrollments) {\n"
                "+            repository.save(enrollment);\n"
                "+        }\n"
            ),
        }
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_real_loop",
        expert_id="performance_reliability",
        title="批量报名从 saveAll 退化为循环逐条保存",
        summary="循环内逐条 repository.save 会把批量写入放大为 N 次。",
        finding_type="direct_defect",
        normalized_issue_type="n_plus_one",
        file_path="src/main/java/com/example/BulkEnrollmentService.java",
        line_start=31,
        code_excerpt=(
            "# src/main/java/com/example/BulkEnrollmentService.java\n"
            "  31 | +        List<CourseEnrollment> enrollments = studentIds.stream()\n"
            "  32 | +            .map(studentId -> CourseEnrollment.create(courseId, studentId))\n"
            "  33 | +            .toList();"
        ),
        confidence=0.88,
        severity="high",
    )
    service.finding_repo.save(review.review_id, finding)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_truncated_loop",
                title="批量报名从 saveAll 退化为循环逐条保存",
                summary="循环内逐条 repository.save 会把批量写入放大为 N 次。",
                normalized_issue_type="n_plus_one",
                file_path=finding.file_path,
                line_start=31,
                finding_ids=[finding.finding_id],
                current_code=(
                    "# src/main/java/com/example/BulkEnrollmentService.java\n"
                    "  31 | +        List<CourseEnrollment> enrollments = studentIds.stream()\n"
                    "  32 | +            .map(studentId -> CourseEnrollment.create(courseId, studentId))\n"
                    "  33 | +            .toList();"
                ),
                suggested_code="repository.saveAll(enrollments);",
                confidence=0.88,
            )
        ],
    )

    issues = service.list_issues(review.review_id)

    assert len(issues) == 1
    assert "for (CourseEnrollment enrollment : enrollments)" in issues[0].current_code
    assert "repository.save(enrollment)" in issues[0].current_code


def test_list_issues_keeps_comment_contract_when_summary_mentions_other_exception(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_comment_with_exception_context",
            "project_id": "proj_comment_with_exception_context",
            "source_ref": "feature/comment-exception-context",
            "target_ref": "main",
            "title": "comment with exception context",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_comment_exception_context",
                title="TODO 承诺未实现，跨聚合副作用缺少建模",
                summary=(
                    "批量报名 TODO 承诺扣减库存并发送预占事件，但当前实现没有对应动作。"
                    "同一 MR 里 PaymentSettlementService 还有 catch 返回 success 的异常问题。"
                ),
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=37,
                current_code=(
                    "# src/main/java/com/example/BulkEnrollmentService.java\n"
                    "  35 | +            repository.save(enrollment);\n"
                    "  36 | +        }\n"
                    "  37 | +        // TODO 批量报名成功后扣减库存并发送预占事件\n"
                    "  38 | +        eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));"
                ),
                suggested_code="repository.saveAll(enrollments);",
                confidence=0.86,
            )
        ],
    )

    issue = service.list_issues(review.review_id)[0]

    assert issue.normalized_issue_type == "comment_contract_unimplemented"
    assert issue.title == "TODO 里的库存扣减未实现"
    assert "扣减库存" in issue.summary


def test_list_display_issues_hides_stale_consistency_failure_after_repair(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_display_consistency",
            "project_id": "proj_display_consistency",
            "source_ref": "feature/display-consistency",
            "target_ref": "main",
            "title": "display consistency",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_exception_repaired",
                title="支付结算失败后仍返回成功",
                summary="PaymentSettlementService 的 catch(RuntimeException ignored) 分支忽略异常并返回 SettlementResult.success。",
                normalized_issue_type="exception_swallowed",
                file_path="src/main/java/com/example/PaymentSettlementService.java",
                line_start=30,
                current_code=(
                    "# src/main/java/com/example/PaymentSettlementService.java\n"
                    "  30 | +        } catch (RuntimeException ignored) {\n"
                    "  31 | +            return SettlementResult.success(payments.size());"
                ),
                suggested_code="} catch (RuntimeException e) {\n    throw e;\n}",
                consistency_check_status="downgraded",
                resolution="consistency_validation_failed",
                consistency_check_summary="原 suggested_code 引用了不存在的变量 enrollments。",
                consistency_conflicts=["suggested_code 中的变量 enrollments 在当前作用域不存在"],
            )
        ],
    )

    issue = service.list_issues(review.review_id)[0]

    assert issue.resolution == "accepted"
    assert issue.status == "resolved"
    assert issue.needs_human is False
    assert issue.consistency_check_status == "repaired"
    assert issue.consistency_conflicts == []
    assert "enrollments" not in issue.consistency_check_summary


def test_list_issues_normalizes_accepted_issue_as_not_needing_human(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_accepted_state",
            "project_id": "proj_accepted_state",
            "source_ref": "feature/accepted-state",
            "target_ref": "main",
            "title": "accepted state",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_accepted_needs_human",
                title="领域事件发布顺序早于聚合持久化",
                summary="CourseCreator 先发布领域事件再保存聚合根。",
                normalized_issue_type="course_creation_semantics",
                file_path="src/main/java/com/example/CourseCreator.java",
                line_start=20,
                current_code=(
                    "# src/main/java/com/example/CourseCreator.java\n"
                    "  20 | +        Course course = new Course(id, name, duration);\n"
                    "  22 |          eventBus.publish(course.pullDomainEvents());\n"
                    "  23 | +        repository.save(course);"
                ),
                suggested_code="repository.save(course);\neventBus.publish(course.pullDomainEvents());",
                status="needs_human",
                needs_human=True,
                resolution="accepted",
                consistency_check_status="repaired",
            )
        ],
    )

    issue = service.list_issues(review.review_id)[0]

    assert issue.status == "resolved"
    assert issue.needs_human is False


def test_list_issues_builds_generic_ddd_creation_suggestion_for_non_course_aggregate(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_order_ddd",
            "project_id": "proj_order_ddd",
            "source_ref": "feature/order-creation",
            "target_ref": "main",
            "title": "order creation",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_order_aggregate_creation",
                title="绕过聚合工厂创建聚合根",
                summary="Order 聚合创建从 Order.create 工厂入口退化为直接构造，并且事件发布早于持久化。",
                normalized_issue_type="course_creation_semantics",
                file_path="src/main/java/com/example/OrderCreator.java",
                line_start=20,
                current_code=(
                    "# src/main/java/com/example/OrderCreator.java\n"
                    "  19 |      public void create(OrderId id, Money amount) {\n"
                    "   - |         Order order = Order.create(id, amount);\n"
                    "  20 | +        Order order = new Order(id, amount);\n"
                    "  21 |\n"
                    "   - |         orderRepository.save(order);\n"
                    "  22 |          eventBus.publish(order.pullDomainEvents());\n"
                    "  23 | +        orderRepository.save(order);\n"
                    "  24 |      }"
                ),
                suggested_code="",
            )
        ],
    )

    issue = service.list_issues(review.review_id)[0]

    assert issue.title == "领域事件发布顺序早于聚合持久化"
    assert "Order.create(id, amount);" in issue.suggested_code
    assert "orderRepository.save(order);" in issue.suggested_code
    assert issue.suggested_code.index("orderRepository.save(order);") < issue.suggested_code.index("eventBus.publish(order.pullDomainEvents());")
    assert "Course.create" not in issue.suggested_code


def test_build_report_supplements_display_finding_when_linked_finding_family_differs(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_display_family",
            "project_id": "proj_display_family",
            "source_ref": "feature/display-family",
            "target_ref": "main",
            "title": "display family coverage",
        }
    )
    shared_finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_shared_bulk",
        expert_id="performance_reliability",
        title="批量写入从 saveAll 退化为循环逐条 repository.save",
        summary="BulkEnrollmentService 在循环内逐条 repository.save，批量输入会被放大为 N 次持久化调用。",
        normalized_issue_type="n_plus_one",
        file_path="src/main/java/com/example/BulkEnrollmentService.java",
        line_start=35,
        code_excerpt=(
            "# src/main/java/com/example/BulkEnrollmentService.java\n"
            "  34 | +        for (CourseEnrollment enrollment : enrollments) {\n"
            "  35 | +            repository.save(enrollment);\n"
            "  36 | +        }\n"
            "  37 | +        // TODO 扣减库存并发送预占事件\n"
        ),
    )
    service.finding_repo.save(review.review_id, shared_finding)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_loop",
                title="批量报名从 saveAll 退化为循环逐条保存",
                summary="批量写入从 saveAll 退化为循环逐条 repository.save。",
                normalized_issue_type="n_plus_one",
                file_path=shared_finding.file_path,
                line_start=35,
                finding_ids=["fdg_shared_bulk"],
                current_code=shared_finding.code_excerpt,
                participant_expert_ids=["performance_reliability"],
                primary_expert_id="performance_reliability",
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_comment",
                title="承诺未落地",
                summary="TODO 承诺扣减库存并发送预占事件，但当前实现没有对应代码。",
                normalized_issue_type="comment_contract_unimplemented",
                file_path=shared_finding.file_path,
                line_start=37,
                finding_ids=["fdg_shared_bulk"],
                current_code=shared_finding.code_excerpt,
                participant_expert_ids=["correctness_business"],
                primary_expert_id="correctness_business",
            ),
        ],
    )

    report = service.build_report(review.review_id)

    families = {(item.normalized_issue_type, item.line_start, item.title) for item in report.findings}
    assert report.issue_count == 2
    assert any(
        issue_type == "n_plus_one" and line_start == 35 and "saveAll" in title and "逐条" in title
        for issue_type, line_start, title in families
    )
    assert ("comment_contract_unimplemented", 37, "TODO 里的库存扣减未实现") in families
    comment_report_finding = next(
        item for item in report.findings if item.normalized_issue_type == "comment_contract_unimplemented"
    )
    assert "扣减库存" in comment_report_finding.summary
    assert "TODO" in comment_report_finding.summary
    comment_report_issue = next(
        item for item in report.issues if item.normalized_issue_type == "comment_contract_unimplemented"
    )
    assert "扣减库存" in comment_report_issue.summary
    assert "TODO" in comment_report_issue.summary


def test_build_report_dedupes_comment_findings_and_prefers_correctness_owner(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_comment_finding_dedupe",
            "project_id": "proj_comment_finding_dedupe",
            "source_ref": "feature/comment-finding-dedupe",
            "target_ref": "main",
            "title": "comment finding dedupe",
        }
    )
    code_excerpt = (
        "# src/main/java/com/example/BulkEnrollmentService.java\n"
        "  37 | +        // TODO 扣减库存并发送预占事件\n"
        "  38 | +        return result;\n"
    )
    service.finding_repo.save_many(
        review.review_id,
        [
            ReviewFinding(
                review_id=review.review_id,
                finding_id="fdg_comment_perf",
                expert_id="performance_reliability",
                title="承诺未落地",
                summary="TODO 承诺扣减库存并发送预占事件，但当前实现没有对应代码。",
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=37,
                confidence=0.91,
                code_excerpt=code_excerpt,
            ),
            ReviewFinding(
                review_id=review.review_id,
                finding_id="fdg_comment_correctness",
                expert_id="correctness_business",
                title="注释承诺未实现",
                summary="TODO 承诺扣减库存并发送预占事件，但当前实现没有对应代码。",
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=37,
                confidence=0.83,
                code_excerpt=code_excerpt,
            ),
        ],
    )

    comment_findings = [
        item
        for item in service.build_report(review.review_id).findings
        if item.normalized_issue_type == "comment_contract_unimplemented"
    ]

    assert len(comment_findings) == 1
    assert comment_findings[0].title == "TODO 里的库存扣减未实现"
    assert comment_findings[0].expert_id == "correctness_business"
    assert comment_findings[0].category_label == "正确性与业务"


def test_build_report_keeps_synchronized_keyword_in_lock_summary(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_lock_keyword",
            "project_id": "proj_lock_keyword",
            "source_ref": "feature/lock-keyword",
            "target_ref": "main",
            "title": "lock keyword",
        }
    )
    service.finding_repo.save_many(
        review.review_id,
        [
            ReviewFinding(
                review_id=review.review_id,
                finding_id="fdg_lock_keyword",
                expert_id="performance_reliability",
                title="并发保护被删除",
                summary="本次 diff 删除了 synchronized、Lock 或分布式锁等并发保护，新增代码没有看到等价保护。",
                finding_type="direct_defect",
                normalized_issue_type="lock_guard_removed",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=26,
                confidence=0.9,
                code_excerpt=(
                    "# src/main/java/com/example/BulkEnrollmentService.java\n"
                    "   - |         synchronized (lock) {\n"
                    "  28 | +        if (studentIds.isEmpty()) {"
                ),
            )
        ],
    )

    lock_finding = next(
        item for item in service.build_report(review.review_id).findings if item.normalized_issue_type == "lock_guard_removed"
    )

    assert "synchronized" in lock_finding.summary
    assert "锁" in lock_finding.summary


def test_list_display_findings_dedupes_query_semantics_variants_same_line(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_query_findings",
            "project_id": "proj_query_findings",
            "source_ref": "feature/query-findings",
            "target_ref": "main",
            "title": "query finding dedupe",
        }
    )
    file_path = "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java"
    service.finding_repo.save_many(
        review.review_id,
        [
            ReviewFinding(
                review_id=review.review_id,
                finding_id="fdg_query_db",
                expert_id="database_analysis",
                title="查询语义从精确匹配退化为模糊匹配",
                summary="equal 改成 like 后结果集扩大。",
                finding_type="direct_defect",
                normalized_issue_type="query_semantics_weakened",
                file_path=file_path,
                line_start=16,
                confidence=0.88,
                code_excerpt='+        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            ),
            ReviewFinding(
                review_id=review.review_id,
                finding_id="fdg_query_sec",
                expert_id="security_compliance",
                title="LIKE 查询未转义通配符，用户可通过特殊字符扩大匹配范围",
                summary="同一行 equal 改成 like 后安全边界字段可能被放宽。",
                finding_type="direct_defect",
                normalized_issue_type="query_semantics_weakened",
                file_path=file_path,
                line_start=16,
                confidence=0.9,
                code_excerpt='+        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            ),
        ],
    )

    findings = service.list_display_findings(review.review_id)

    query_findings = [
        item
        for item in findings
        if item.file_path == file_path and item.line_start == 16 and item.normalized_issue_type == "query_semantics_weakened"
    ]
    assert len(query_findings) == 1
    assert query_findings[0].title == "查询语义从精确匹配退化为模糊匹配"


def test_list_display_findings_sanitizes_internal_remediation_text(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_display_finding_sanitize",
            "project_id": "proj_display_finding_sanitize",
            "source_ref": "feature/display-finding-sanitize",
            "target_ref": "main",
            "title": "display finding sanitize",
        }
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_display_sanitize",
            expert_id="correctness_business",
            title="承诺未落地",
            summary="TODO 承诺扣减库存并发送预占事件，但当前实现没有对应代码。",
            normalized_issue_type="comment_contract_unimplemented",
            file_path="src/main/java/com/example/BulkEnrollmentService.java",
            line_start=37,
            confidence_rationale="确定性 Java 质量信号；直接代码证据；无需依赖额外条件确认",
            remediation_strategy="回到当前代码锚点，补齐被规则命中的真实业务逻辑或保护逻辑。",
            remediation_suggestion="按当前代码片段补齐缺失实现，并增加能复现该风险的回归测试。",
            remediation_steps=["在当前代码锚点补齐缺失的业务逻辑或保护逻辑", "识别批量输入规模", "补齐 TODO 或注释承诺的业务动作"],
            code_excerpt=(
                "# src/main/java/com/example/BulkEnrollmentService.java\n"
                "  37 | +        // TODO 扣减库存并发送预占事件\n"
                "  38 | +        return result;\n"
            ),
        ),
    )

    finding = service.list_display_findings(review.review_id)[0]
    rendered = "\n".join(
        [
            finding.remediation_strategy,
            finding.remediation_suggestion,
            *finding.remediation_steps,
        ]
    )

    assert "当前代码锚点" not in rendered
    assert "按当前代码片段" not in rendered
    assert "识别批量输入规模" not in rendered
    assert "额外条件" not in finding.confidence_rationale
    assert finding.confidence_rationale == "确定性 Java 质量信号；直接代码证据"
    assert finding.remediation_steps == ["补齐 TODO 或注释承诺的业务动作"]


def test_list_display_findings_disambiguates_same_text_across_anchors(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_finding_duplicate_display",
            "project_id": "proj_finding_duplicate_display",
            "source_ref": "feature/finding-duplicate-display",
            "target_ref": "main",
            "title": "finding duplicate display",
        }
    )
    shared_title = "批量保存改成了循环逐条保存"
    shared_summary = "当前批量路径在循环内逐条保存，批量输入会被放大为多次数据库访问。"
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_first_loop",
            expert_id="performance_reliability",
            title=shared_title,
            summary=shared_summary,
            finding_type="direct_defect",
            normalized_issue_type="n_plus_one",
            file_path="src/main/java/com/example/a/BatchService.java",
            line_start=35,
            code_excerpt=(
                "35 | +        for (Order order : orders) {\n"
                "36 | +            orderRepository.save(order);\n"
                "37 | +        }"
            ),
            confidence=0.9,
        ),
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_second_loop",
            expert_id="performance_reliability",
            title=shared_title,
            summary=shared_summary,
            finding_type="direct_defect",
            normalized_issue_type="n_plus_one",
            file_path="src/main/java/com/example/b/BatchService.java",
            line_start=35,
            code_excerpt=(
                "35 | +        for (Payment payment : payments) {\n"
                "36 | +            paymentRepository.save(payment);\n"
                "37 | +        }"
            ),
            confidence=0.88,
        ),
    )

    findings = service.list_display_findings(review.review_id)
    report_findings = service.build_report(review.review_id).findings

    assert len(findings) == 2
    assert len(report_findings) == 2
    titles = {finding.file_path: finding.title for finding in findings}
    summaries = {finding.file_path: finding.summary for finding in findings}
    assert "example/a/BatchService.java 第 36 行" in titles["src/main/java/com/example/a/BatchService.java"]
    assert "example/b/BatchService.java 第 36 行" in titles["src/main/java/com/example/b/BatchService.java"]
    assert summaries["src/main/java/com/example/a/BatchService.java"] != summaries[
        "src/main/java/com/example/b/BatchService.java"
    ]
    assert report_findings[0].title != report_findings[1].title


def test_build_report_uses_target_line_not_neighbor_todo_for_finding_family(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_loop_with_neighbor_todo",
            "project_id": "proj_loop_with_neighbor_todo",
            "source_ref": "feature/loop-with-neighbor-todo",
            "target_ref": "main",
            "title": "loop finding with neighbor todo",
        }
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_loop_polluted_by_todo",
            expert_id="performance_reliability",
            title="批处理写入被退化为逐条写入，吞吐能力严重退化",
            summary="批处理写入被替换为逐条循环写入，吞吐能力退化。",
            finding_type="direct_defect",
            normalized_issue_type="comment_contract_unimplemented",
            file_path="src/main/java/com/example/BulkEnrollmentService.java",
            line_start=35,
            code_excerpt=(
                "# src/main/java/com/example/BulkEnrollmentService.java\n"
                "  34 | +        for (CourseEnrollment enrollment : enrollments) {\n"
                "  35 | +            repository.save(enrollment);\n"
                "  36 | +        }\n"
                "  37 | +        // TODO 批量报名成功后扣减库存并发送预占事件\n"
            ),
            confidence=0.91,
        ),
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_comment_type_on_non_comment_line",
            expert_id="performance_reliability",
            title="循环调用放大",
            summary="当前实现把外部依赖调用放进循环路径（for (...) / repository.save）。",
            finding_type="direct_defect",
            normalized_issue_type="comment_contract_unimplemented",
            file_path="src/main/java/com/example/BulkEnrollmentService.java",
            line_start=31,
            code_excerpt=(
                "# src/main/java/com/example/BulkEnrollmentService.java\n"
                "  29 | +            return;\n"
                "  30 |          }\n"
                "  31 | +        List<CourseEnrollment> enrollments = studentIds.stream()\n"
                "  32 | +            .map(studentId -> CourseEnrollment.create(courseId, studentId))\n"
                "  33 | +            .toList();\n"
            ),
            confidence=0.89,
        ),
    )

    findings = service.build_report(review.review_id).findings
    finding = findings[0]

    assert len(findings) == 1
    assert finding.normalized_issue_type == "n_plus_one"
    assert finding.title == "批量报名从 saveAll 退化为循环逐条保存"
    assert "TODO" not in finding.title
    assert "TODO" not in finding.summary
    assert "库存扣减" not in finding.summary


def test_list_issues_dedupes_same_display_root_cause(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_dedupe",
            "project_id": "proj_dedupe",
            "source_ref": "feature/dedupe",
            "target_ref": "main",
            "title": "dedupe display issues",
        }
    )
    issue_a = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_dup_a",
        title="承诺未落地",
        summary="createOrder 的注释承诺先校验库存并加锁防止超卖，但当前实现直接创建订单、保存并发布事件，缺少并发库存保护。",
        normalized_issue_type="comment_contract_unimplemented",
        file_path="src/main/java/com/example/order/OrderService.java",
        line_start=32,
        finding_ids=["fdg_a"],
        participant_expert_ids=["correctness_business"],
        confidence=0.72,
    )
    issue_b = issue_a.model_copy(
        update={
            "issue_id": "iss_dup_b",
            "finding_ids": ["fdg_b"],
            "participant_expert_ids": ["database_analysis"],
            "confidence": 0.86,
        }
    )
    service.issue_repo.save_all(review.review_id, [issue_a, issue_b])

    issues = service.list_issues(review.review_id)

    assert len(issues) == 1
    assert issues[0].finding_ids == ["fdg_a", "fdg_b"]
    assert issues[0].participant_expert_ids == ["correctness_business", "database_analysis"]
    assert issues[0].confidence == 0.86


def test_list_issues_disambiguates_same_summary_across_different_files(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_same_summary",
            "project_id": "proj_same_summary",
            "source_ref": "feature/same-summary",
            "target_ref": "main",
            "title": "same summary display",
        }
    )
    shared_summary = "本次 diff 新增或保留了 TODO/注释承诺，但当前实现没有对应业务动作，调用方会误以为该能力已经落地。"
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_order_contract",
                title="承诺未落地",
                summary=shared_summary,
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/order/OrderService.java",
                line_start=32,
                current_code="32 | +        // TODO: 扣减库存并发送订单事件\n33 | +        return orderRepository.save(order);",
                confidence=0.86,
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_payment_contract",
                title="承诺未落地",
                summary=shared_summary,
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/payment/PaymentService.java",
                line_start=32,
                current_code="32 | +        // TODO: 记录审计日志并发送支付事件\n33 | +        return paymentRepository.save(payment);",
                confidence=0.86,
            ),
        ],
    )

    issues = service.list_issues(review.review_id)

    assert len(issues) == 2
    summaries = {issue.file_path: issue.summary for issue in issues}
    assert "OrderService.java 第 32 行" in summaries["src/main/java/com/example/order/OrderService.java"]
    assert "PaymentService.java 第 32 行" in summaries["src/main/java/com/example/payment/PaymentService.java"]
    assert summaries["src/main/java/com/example/order/OrderService.java"] != summaries[
        "src/main/java/com/example/payment/PaymentService.java"
    ]


def test_list_issues_disambiguates_same_title_across_different_files(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_same_title",
            "project_id": "proj_same_title",
            "source_ref": "feature/same-title",
            "target_ref": "main",
            "title": "same title display",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_payment_loop",
                title="批量写入从 saveAll 退化为循环逐条 repository.save",
                summary="PaymentSettlementService 将 paymentRepository.saveAll 改成循环内逐条 paymentRepository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/payment/PaymentSettlementService.java",
                line_start=28,
                current_code=(
                    "28 | +        for (Payment payment : payments) {\n"
                    "29 | +            paymentRepository.save(payment);\n"
                    "30 | +        }"
                ),
                confidence=0.9,
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_enroll_loop",
                title="批量写入从 saveAll 退化为循环逐条 repository.save",
                summary="BulkEnrollmentService 将 repository.saveAll 改成循环内逐条 repository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/enroll/BulkEnrollmentService.java",
                line_start=35,
                current_code=(
                    "35 | +        for (CourseEnrollment enrollment : enrollments) {\n"
                    "36 | +            repository.save(enrollment);\n"
                    "37 | +        }"
                ),
                confidence=0.9,
            ),
        ],
    )

    issues = service.list_issues(review.review_id)

    assert len(issues) == 2
    titles = {issue.file_path: issue.title for issue in issues}
    assert "PaymentSettlementService.java 第 29 行" in titles[
        "src/main/java/com/example/payment/PaymentSettlementService.java"
    ]
    assert "BulkEnrollmentService.java 第 36 行" in titles[
        "src/main/java/com/example/enroll/BulkEnrollmentService.java"
    ]
    assert titles["src/main/java/com/example/payment/PaymentSettlementService.java"] != titles[
        "src/main/java/com/example/enroll/BulkEnrollmentService.java"
    ]


def test_list_issues_disambiguates_windows_path_title_with_basename(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_same_title_windows",
            "project_id": "proj_same_title_windows",
            "source_ref": "feature/same-title-windows",
            "target_ref": "main",
            "title": "same title windows display",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_payment_loop_windows",
                title="批量写入从 saveAll 退化为循环逐条 repository.save",
                summary="PaymentSettlementService 将 paymentRepository.saveAll 改成循环内逐条 paymentRepository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src\\main\\java\\com\\example\\payment\\PaymentSettlementService.java",
                line_start=28,
                current_code=(
                    "28 | +        for (Payment payment : payments) {\n"
                    "29 | +            paymentRepository.save(payment);\n"
                    "30 | +        }"
                ),
                confidence=0.9,
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_enroll_loop_windows",
                title="批量写入从 saveAll 退化为循环逐条 repository.save",
                summary="BulkEnrollmentService 将 repository.saveAll 改成循环内逐条 repository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src\\main\\java\\com\\example\\enroll\\BulkEnrollmentService.java",
                line_start=35,
                current_code=(
                    "35 | +        for (CourseEnrollment enrollment : enrollments) {\n"
                    "36 | +            repository.save(enrollment);\n"
                    "37 | +        }"
                ),
                confidence=0.9,
            ),
        ],
    )

    issues = service.list_issues(review.review_id)

    assert len(issues) == 2
    titles = {issue.file_path: issue.title for issue in issues}
    assert titles["src\\main\\java\\com\\example\\payment\\PaymentSettlementService.java"].startswith(
        "PaymentSettlementService.java 第 29 行"
    )
    assert titles["src\\main\\java\\com\\example\\enroll\\BulkEnrollmentService.java"].startswith(
        "BulkEnrollmentService.java 第 36 行"
    )
    assert "src\\main\\java" not in titles["src\\main\\java\\com\\example\\payment\\PaymentSettlementService.java"]
    assert "src\\main\\java" not in titles["src\\main\\java\\com\\example\\enroll\\BulkEnrollmentService.java"]


def test_list_display_issues_disambiguates_light_report_canonical_titles(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_light_title_duplicate",
            "project_id": "proj_light_title_duplicate",
            "source_ref": "feature/light-title-duplicate",
            "target_ref": "main",
            "title": "light report title duplicate",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_first_batch_loop",
                title="批量写入从 saveAll 退化为循环逐条 repository.save",
                summary="第一个批量入口在循环内逐条 repository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/a/BatchService.java",
                line_start=35,
                current_code=(
                    "35 | +        for (Order order : orders) {\n"
                    "36 | +            orderRepository.save(order);\n"
                    "37 | +        }"
                ),
                confidence=0.9,
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_second_batch_loop",
                title="批量写入从 saveAll 退化为循环逐条 repository.save",
                summary="第二个批量入口在循环内逐条 repository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/b/BatchService.java",
                line_start=35,
                current_code=(
                    "35 | +        for (Payment payment : payments) {\n"
                    "36 | +            paymentRepository.save(payment);\n"
                    "37 | +        }"
                ),
                confidence=0.88,
            ),
        ],
    )

    issues = service.list_display_issues(review.review_id)
    report_issues = service.build_report(review.review_id).issues

    assert len(issues) == 2
    assert len(report_issues) == 2
    titles = {issue.file_path: issue.title for issue in issues}
    assert "example/a/BatchService.java 第 36 行" in titles["src/main/java/com/example/a/BatchService.java"]
    assert "example/b/BatchService.java 第 36 行" in titles["src/main/java/com/example/b/BatchService.java"]
    assert titles["src/main/java/com/example/a/BatchService.java"] != titles[
        "src/main/java/com/example/b/BatchService.java"
    ]
    assert report_issues[0].title != report_issues[1].title


def test_list_issues_keeps_same_file_different_loop_anchors_separate(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_same_file_loop",
            "project_id": "proj_same_file_loop",
            "source_ref": "feature/same-file-loop",
            "target_ref": "main",
            "title": "same file loop anchors",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_first_loop",
                title="批量写入从 saveAll 退化为循环逐条 repository.save",
                summary="第一个批量入口在循环内逐条 repository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/BatchService.java",
                line_start=30,
                current_code=(
                    "30 | +        for (Order order : orders) {\n"
                    "31 | +            orderRepository.save(order);\n"
                    "32 | +        }"
                ),
                confidence=0.9,
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_second_loop",
                title="批量写入从 saveAll 退化为循环逐条 repository.save",
                summary="第二个批量入口在循环内逐条 repository.save。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/BatchService.java",
                line_start=68,
                current_code=(
                    "68 | +        for (Payment payment : payments) {\n"
                    "69 | +            paymentRepository.save(payment);\n"
                    "70 | +        }"
                ),
                confidence=0.88,
            ),
        ],
    )

    issues = service.list_issues(review.review_id)

    assert len(issues) == 2
    assert {issue.line_start for issue in issues} == {31, 69}
    assert all("BatchService.java 第" in issue.title for issue in issues)


def test_list_issues_does_not_resurrect_below_threshold_finding(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    service.update_runtime_settings(
        {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "issue_confidence_threshold_p2": 0.8,
        }
    )
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_threshold_display",
            "project_id": "proj_threshold_display",
            "source_ref": "feature/threshold-display",
            "target_ref": "main",
            "title": "threshold display",
        }
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_low_conf_loop",
            expert_id="performance_reliability",
            title="批量保存改成循环逐条保存",
            summary="循环内逐条 repository.save，批量输入会放大为 N 次持久化调用。",
            normalized_issue_type="n_plus_one",
            severity="medium",
            confidence=0.4,
            file_path="src/main/java/com/example/BulkEnrollmentService.java",
            line_start=35,
            code_excerpt=(
                "  34 | +        for (CourseEnrollment enrollment : enrollments) {\n"
                "  35 | +            repository.save(enrollment);\n"
                "  36 | +        }\n"
            ),
        ),
    )

    assert service.list_issues(review.review_id) == []
    report = service.build_report(review.review_id)
    assert report.issue_count == 0


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


def test_build_report_cross_links_impact_report_to_review_issues(storage_root: Path):
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
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_order_controller",
                title="订单创建入口缺少鉴权",
                summary="OrderController.createOrder 缺少资源级鉴权。",
                file_path="order-service/src/main/java/com/example/OrderController.java",
                line_start=10,
                normalized_issue_type="auth_bypass",
            )
        ],
    )

    report = service.build_report(review.review_id)

    assert report.impact_report is not None
    assert "iss_order_controller" in report.impact_report.related_issue_ids
    assert any(link.issue_id == "iss_order_controller" for link in report.impact_report.impact_issue_links)
    assert any("iss_order_controller" in item for item in report.impact_report.manual_verification)


def test_build_report_does_not_emit_fallback_after_gitnexus_failure(storage_root: Path):
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
    metadata = dict(review.subject.metadata or {})
    metadata["impact_analysis_progress"] = {
        "state": "failed",
        "graph_status": "failed",
        "error_message": "GitNexus MCP unavailable",
    }
    service.review_repo.save(review.model_copy(update={"subject": review.subject.model_copy(update={"metadata": metadata})}))

    report = service.build_report(review.review_id)

    assert report.impact_report is None


def test_build_report_quality_summary_audits_display_issues_after_cleanup(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_quality_summary",
            "project_id": "proj_quality_summary",
            "source_ref": "feature/quality-summary",
            "target_ref": "main",
            "title": "quality summary",
            "selected_experts": ["performance_reliability"],
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_loop_weak_remediation",
                title="批量保存改成循环逐条保存",
                summary="循环内逐条 repository.save，批量输入会放大为 N 次持久化调用。",
                normalized_issue_type="n_plus_one",
                primary_expert_id="performance_reliability",
                participant_expert_ids=["performance_reliability"],
                severity="medium",
                confidence=0.9,
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=35,
                current_code=(
                    "  34 | +        for (CourseEnrollment enrollment : enrollments) {\n"
                    "  35 | +            repository.save(enrollment);\n"
                    "  36 | +        }\n"
                ),
                remediation_suggestion="结合本条问题说明和修改思路处理。",
                suggested_code="repository.saveAll(enrollments);",
            )
        ],
    )

    report = service.build_report(review.review_id)

    assert "结合本条问题说明" not in report.issues[0].remediation_suggestion
    assert report.confidence_summary.quality_gate_passed is True
    assert report.confidence_summary.fallback_text_failure_count == 0
    assert report.confidence_summary.finding_issue_family_mismatch_count == 0


def test_build_report_display_quality_gate_removes_placeholder_text(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_placeholder_display",
            "project_id": "proj_placeholder_display",
            "source_ref": "feature/placeholder-display",
            "target_ref": "main",
            "title": "placeholder display cleanup",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_placeholder_display",
                title="胆量问题",
                summary="需要确定其他条件后才能判断是否存在问题。",
                problem_description="当前未生成可直接落地的建议代码，请结合本条问题说明和修改思路处理。",
                normalized_issue_type="direct_defect",
                severity="medium",
                confidence=0.81,
                file_path="src/main/java/com/example/OrderService.java",
                line_start=21,
                current_code=(
                    "# src/main/java/com/example/OrderService.java\n"
                    "  20 | +        Order order = new Order(request.getSkuId(), request.getQuantity());\n"
                    "  21 | +        orderRepository.save(order);"
                ),
                remediation_strategy="建议完善处理。",
                remediation_suggestion="当前未生成可直接落地的建议代码，请结合本条问题说明和修改思路处理。",
                suggested_code="public void unrelated() {\n    return;\n}",
            )
        ],
    )

    report_issue = service.build_report(review.review_id).issues[0]

    display_text = "\n".join(
        [
            report_issue.title,
            report_issue.summary,
            report_issue.remediation_strategy,
            report_issue.remediation_suggestion,
            report_issue.suggested_code,
        ]
    )
    assert "胆量问题" not in display_text
    assert "需要确定其他条件" not in display_text
    assert "当前未生成可直接落地的建议代码" not in display_text
    assert "结合本条问题说明" not in display_text
    assert "OrderService.java" in report_issue.summary
    assert "第 21 行" in report_issue.summary
    assert report_issue.suggested_code == ""


def test_build_report_display_quality_gate_hides_unrelated_suggested_code(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_unrelated_suggestion",
            "project_id": "proj_unrelated_suggestion",
            "source_ref": "feature/unrelated-suggestion",
            "target_ref": "main",
            "title": "unrelated suggestion cleanup",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_unrelated_suggestion",
                title="批量订单保存退化为循环逐条保存",
                summary="OrderService 第 42 行在循环内逐条调用 orderRepository.save，批量输入会放大为 N 次数据库访问。",
                normalized_issue_type="n_plus_one",
                severity="high",
                confidence=0.88,
                primary_expert_id="performance_reliability",
                participant_expert_ids=["performance_reliability"],
                file_path="src/main/java/com/example/OrderService.java",
                line_start=42,
                current_code=(
                    "# src/main/java/com/example/OrderService.java\n"
                    "  41 | +        for (Order order : orders) {\n"
                    "  42 | +            orderRepository.save(order);\n"
                    "  43 | +        }"
                ),
                remediation_suggestion="把循环内逐条保存改为批量保存，并增加大批量输入的回归测试。",
                suggested_code=(
                    "public void refreshCache(CacheKey key) {\n"
                    "    cacheClient.evict(key);\n"
                    "    return;\n"
                    "}"
                ),
            )
        ],
    )

    report_issue = service.build_report(review.review_id).issues[0]

    assert "批量" in report_issue.title
    assert "orderRepository.save" in report_issue.current_code
    assert report_issue.suggested_code == ""
    assert report_issue.remediation_filtered is True


def test_build_report_normalizes_query_semantics_finding_remediation(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_query_semantics_finding",
            "project_id": "proj_query_semantics_finding",
            "source_ref": "feature/query-semantics-finding",
            "target_ref": "main",
            "title": "query semantics finding",
        }
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_query_semantics",
            expert_id="security_compliance",
            title="查询语义从精确匹配退化为模糊匹配",
            summary="builder.equal 被改成 builder.like，查询语义被静默放宽。建议：补齐缺失实现，并增加能复现该风险的回归测试。",
            normalized_issue_type="query_semantics_weakened",
            severity="high",
            confidence=0.9,
            file_path="src/shared/HibernateCriteriaConverter.java",
            line_start=16,
            code_excerpt='  16 | +        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            remediation_suggestion="补齐缺失实现，并增加能复现该风险的回归测试。",
            suggested_code="private Predicate equalsPredicateTransformer(Filter filter, Root<T> root) {\n    return builder.equal(root.get(filter.field().value()), filter.value().value());\n}",
        ),
    )

    report = service.build_report(review.review_id)

    assert "补齐缺失实现" not in report.findings[0].summary
    assert "like" in report.findings[0].summary
    assert report.findings[0].remediation_suggestion.startswith("若 equalsPredicateTransformer")
    assert report.confidence_summary.fallback_text_failure_count == 0


def test_build_report_quality_summary_flags_expected_expert_not_selected(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_quality_expected_expert",
            "project_id": "proj_quality_expected_expert",
            "source_ref": "feature/expected-expert",
            "target_ref": "main",
            "title": "expected expert",
            "selected_experts": ["correctness_business"],
            "metadata": {
                "expected_required_experts": ["correctness_business", "security_compliance"],
            },
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_business",
                title="注释或 TODO 写了要做，但代码没有对应实现",
                summary="第 19 行 TODO 承诺补充权限校验，但当前代码没有对应实现。",
                normalized_issue_type="comment_contract_unimplemented",
                primary_expert_id="correctness_business",
                participant_expert_ids=["correctness_business"],
                severity="medium",
                confidence=0.9,
                file_path="src/main/java/com/example/OrderService.java",
                line_start=19,
                current_code="  19 | +        // TODO 补充当前登录用户权限校验",
                remediation_suggestion="补齐 TODO 或注释承诺的业务动作；如果本次不交付该能力，应删除误导性注释并拆出明确任务。",
                suggested_code="if (!permissionService.canRead(currentUser, orderId)) {\n    throw new ForbiddenException();\n}",
            )
        ],
    )

    report = service.build_report(review.review_id)

    assert report.confidence_summary.quality_gate_passed is False
    assert report.confidence_summary.security_expert_activated is False
    assert report.confidence_summary.business_expert_activated is True
    assert report.confidence_summary.expert_activation_missing_count == 1


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


def test_build_report_merges_same_anchor_query_semantics_issues_from_different_experts(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_query_duplicate",
            "project_id": "proj_query_duplicate",
            "source_ref": "feature/query-duplicate",
            "target_ref": "main",
            "title": "query duplicate",
        }
    )
    current_code = (
        "# src/shared/HibernateCriteriaConverter.java\n"
        "  15 |      private Predicate equalsPredicateTransformer(Filter filter, Root<T> root) {\n"
        "  16 | +        return builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()));\n"
        "  17 |      }"
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_query_security",
                title="查询语义从精确匹配退化为模糊匹配",
                summary="安全专家发现 equal 被改成 like，权限过滤范围可能被放大。",
                normalized_issue_type="query_semantics_regression",
                primary_expert_id="security_compliance",
                participant_expert_ids=["security_compliance"],
                file_path="src/shared/HibernateCriteriaConverter.java",
                line_start=16,
                current_code=current_code,
                finding_ids=["fdg_query_security"],
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_query_database",
                title="查询语义从精确匹配退化为模糊匹配",
                summary="数据库专家发现同一行 like 会导致索引使用和查询结果范围变化。",
                normalized_issue_type="query_semantics_regression",
                primary_expert_id="database_analysis",
                participant_expert_ids=["database_analysis"],
                file_path="src/shared/HibernateCriteriaConverter.java",
                line_start=16,
                current_code=current_code,
                finding_ids=["fdg_query_database"],
            ),
        ],
    )

    report = service.build_report(review.review_id)

    assert report.issue_count == 1
    assert len(report.issues) == 1
    assert set(report.issues[0].finding_ids) == {"fdg_query_security", "fdg_query_database"}
    assert "database_analysis" in report.issues[0].participant_expert_ids
    assert report.issues[0].title == "查询语义从精确匹配退化为模糊匹配"


def test_list_issues_merges_same_file_when_windows_path_separators_differ(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_windows_path",
            "project_id": "proj_windows_path",
            "source_ref": "feature/windows-path-dedupe",
            "target_ref": "main",
            "title": "windows path dedupe",
        }
    )
    issue_a = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_windows_path_a",
        title="批量报名从 saveAll 退化为循环逐条保存",
        summary="循环内逐条 repository.save 会放大批量保存成本。",
        normalized_issue_type="n_plus_one",
        file_path="src\\main\\java\\com\\example\\BulkEnrollmentService.java",
        line_start=35,
        finding_ids=["fdg_windows_path_a"],
        participant_expert_ids=["performance_reliability"],
    )
    issue_b = issue_a.model_copy(
        update={
            "issue_id": "iss_windows_path_b",
            "file_path": "src/main/java/com/example/BulkEnrollmentService.java",
            "finding_ids": ["fdg_windows_path_b"],
            "participant_expert_ids": ["database_analysis"],
            "summary": "同一行循环内调用 repository.save，批量输入会被放大为多次数据库访问。",
        }
    )
    service.issue_repo.save_all(review.review_id, [issue_a, issue_b])

    issues = service.list_issues(review.review_id)
    report = service.build_report(review.review_id)

    assert len(issues) == 1
    assert issues[0].finding_ids == ["fdg_windows_path_a", "fdg_windows_path_b"]
    assert issues[0].participant_expert_ids == ["performance_reliability", "database_analysis"]
    assert report.issue_count == 1


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
                summary=(
                    "HibernateCriteriaConverter.java:63 定位到“查询语义从精确匹配退化为模糊匹配”，"
                    "修复时应围绕该问题位置处理。 建议：补齐缺失实现，并增加能复现该风险的回归测试。 "
                    "模糊匹配未对特殊字符转义，可能导致查询结果偏差。"
                ),
                normalized_issue_type="magic_value_overuse",
                file_path=finding.file_path,
                line_start=63,
                current_code=(
                    "private Predicate equalsPredicateTransformer(Filter filter, Root<T> root) {\n"
                    "    return builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()));\n"
                    "}"
                ),
                remediation_strategy="围绕本条问题指向的位置，补齐缺失的业务逻辑或保护逻辑。",
                remediation_suggestion="补齐缺失实现，并增加能复现该风险的回归测试。",
                remediation_steps=["补齐缺失实现，并增加能复现该风险的回归测试。"],
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
    assert "补齐缺失实现" not in report_issue.summary
    assert "把 equal 精确匹配改成 like/contains 模糊匹配" in report_issue.summary
    assert report_issue.remediation_steps[:2] == [
        "确认该过滤器是否仍应执行精确匹配；如果是，请恢复 equal/等值查询。",
        "如果产品确实需要模糊搜索，请新增明确的 contains/like 操作符，并补充通配符转义和权限边界测试。",
    ]
    assert "恢复 builder.equal" in report_issue.remediation_suggestion


def test_list_issues_replaces_weak_loop_remediation_for_report(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_loop_display",
            "project_id": "proj_loop_display",
            "source_ref": "feature/loop-display",
            "target_ref": "main",
            "title": "loop display",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_loop_display",
                title="批量报名从 saveAll 退化为循环逐条保存",
                summary="批处理写入被替换为逐条循环写入，吞吐能力退化。",
                normalized_issue_type="n_plus_one",
                file_path="src/main/java/com/example/BulkEnrollmentService.java",
                line_start=35,
                current_code=(
                    "# src/main/java/com/example/BulkEnrollmentService.java\n"
                    "  34 | +        for (CourseEnrollment enrollment : enrollments) {\n"
                    "  35 | +            repository.save(enrollment);\n"
                    "  36 | +        }"
                ),
                remediation_suggestion="补齐缺失实现，并增加能复现该风险的回归测试。",
                remediation_steps=[
                    "把循环内逐条访问改为批量查询、批量保存或固定窗口批处理。",
                    "补充大批量输入下的调用次数和耗时回归测试。",
                    "把循环内逐条访问改为批量查询、批量保存或固定窗口批处理。",
                ],
            )
        ],
    )

    report_issue = service.build_report(review.review_id).issues[0]

    assert report_issue.normalized_issue_type == "n_plus_one"
    assert "补齐缺失" not in report_issue.remediation_strategy
    assert "repository.save" in report_issue.remediation_strategy
    assert "补齐缺失实现" not in report_issue.remediation_suggestion
    assert "repository.save" in report_issue.remediation_suggestion
    assert "saveAll" in report_issue.remediation_suggestion
    assert report_issue.remediation_steps == [
        "把循环内逐条访问改为批量查询、批量保存或固定窗口批处理。",
        "补充大批量输入下的调用次数和耗时回归测试。",
    ]


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
    assert issue.title == "注释承诺未实现"
    assert report_issue.normalized_issue_type == "comment_contract_unimplemented"
    assert report_issue.title == "注释承诺未实现"


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


def test_needs_verification_issue_is_not_returned_as_effective_issue(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_4",
            "project_id": "proj_4",
            "source_ref": "feature/needs-verification",
            "target_ref": "main",
            "title": "needs verification is not effective issue",
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_needs_verification",
                title="跨文件契约可能未同步",
                summary="签名变化需要继续验证。",
                finding_type="risk_hypothesis",
                status="needs_verification",
                resolution="llm_judge_needs_verification",
                file_path="src/main/java/com/example/OwnerRepository.java",
                line_start=10,
                llm_judge_result={
                    "final_verdict": "needs_verification",
                    "reason": "跨文件证据存在，但调用方上下文还不完整",
                },
            )
        ],
    )

    report = service.build_report(review.review_id)

    assert service.list_issues(review.review_id) == []
    assert report.issues == []
    assert report.issue_count == 0
    assert report.confidence_summary.llm_judged_issue_count == 1
    assert report.confidence_summary.llm_judge_needs_verification_count == 1


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


def test_build_report_humanizes_conditional_filter_decision(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_filter_text",
            "project_id": "proj_filter_text",
            "source_ref": "feature/filter-text",
            "target_ref": "main",
            "title": "filter text",
        }
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="main_agent",
            message_type="issue_filter_applied",
            content="conditional finding filtered",
            metadata={
                "issue_filter_decisions": [
                    {
                        "topic": "fdg_conditional",
                        "rule_code": "conditional_conclusion",
                        "rule_label": "待验证结论保留为 finding",
                        "reason": "当前问题仍依赖额外条件或上下文确认，暂不升级为有效 issue，仅保留为 finding。",
                        "severity": "high",
                        "finding_ids": ["fdg_conditional"],
                        "finding_titles": ["循环调用放大"],
                        "expert_ids": ["performance_reliability"],
                    }
                ]
            },
        )
    )

    decision = service.build_report(review.review_id).issue_filter_decisions[0]

    assert decision.rule_label == "证据未闭环，保留为观察项"
    assert decision.reason == "这条发现已有代码线索，但证据还不足以作为正式问题提交；系统先保留在观察清单中，供人工复核时参考。"
    assert "额外条件" not in decision.reason
    assert "待验证结论" not in decision.rule_label


def test_build_report_exposes_quality_governance_stats(storage_root: Path):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_quality",
            "project_id": "proj_quality",
            "source_ref": "feature/quality-summary",
            "target_ref": "main",
            "title": "quality summary",
            "metadata": {
                "review_policy": {
                    "excluded_changed_files": ["dist/app.js"],
                    "reviewable_changed_files": ["backend/app/payment.py", "backend/app/auth.py"],
                    "required_experts": ["security_compliance", "database_analysis"],
                    "path_rules": [
                        {
                            "pattern": "backend/app/payments/**",
                            "required_experts": ["security_compliance"],
                        }
                    ],
                }
            },
        }
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_with_chain",
                title="授权校验缺失",
                summary="支付接口新增路径未校验操作者身份。",
                file_path="backend/app/payment.py",
                line_start=42,
                evidence_chain=[
                    {"step": "claim", "status": "present"},
                    {"step": "anchor", "status": "present"},
                ],
            ),
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_without_chain",
                title="缺少测试",
                summary="新增边界条件未覆盖。",
                file_path="backend/app/auth.py",
                line_start=18,
            ),
        ],
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="judge",
            message_type="issue_filter_applied",
            content="Quality governance filters applied",
            metadata={
                "phase": "coordination",
                "issue_filter_decisions": [
                    {
                        "topic": "低置信度建议",
                        "rule_code": "below_issue_priority_threshold",
                        "rule_label": "低优先级过滤",
                    },
                    {
                        "topic": "样式噪声",
                        "rule_code": "low_confidence_noise",
                        "rule_label": "低置信噪声过滤",
                    },
                    {
                        "topic": "超出评论预算",
                        "rule_code": "repo_policy_comment_budget",
                        "rule_label": "仓库评论预算",
                    },
                ],
            },
        )
    )

    report = service.build_report(review.review_id)

    assert report.confidence_summary.evidence_chain_issue_count == 1
    assert report.confidence_summary.evidence_chain_coverage == 0.5
    assert report.confidence_summary.quality_filtered_issue_count == 2
    assert report.confidence_summary.policy_comment_budget_filtered_count == 1
    assert report.confidence_summary.review_policy_excluded_file_count == 1
    assert report.confidence_summary.review_policy_reviewable_file_count == 2
    assert report.confidence_summary.review_policy_path_rule_count == 1
    assert report.confidence_summary.review_policy_required_expert_count == 2
