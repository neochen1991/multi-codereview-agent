#!/usr/bin/env python3
"""Seed a realistic review record for frontend quality and impact smoke tests."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.models.event import ReviewEvent
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage
from app.services.review_service import ReviewService


def build_demo_diff() -> str:
    return """diff --git a/src/main/java/com/acme/order/OrderService.java b/src/main/java/com/acme/order/OrderService.java
@@ -39,6 +39,8 @@ public class OrderService {
   public Order placeOrder(CreateOrderCommand command) {
     InventoryReservation inventory = inventoryService.reserve(command.items());
+    PaymentReservation payment = paymentClient.reserve(command.payment());
+    auditLogger.info("payment reserved token={} card={}", command.payment().token(), command.payment().cardNo());
     Order order = orderRepository.save(Order.create(command, inventory));
     eventPublisher.publishCreated(order);
     return order;
diff --git a/src/main/java/com/acme/order/OrderController.java b/src/main/java/com/acme/order/OrderController.java
@@ -28,6 +28,13 @@ public class OrderController {
   public OrderResponse create(@RequestBody CreateOrderRequest request) {
     return mapper.toResponse(orderService.placeOrder(mapper.toCommand(request)));
   }
+
+  @PostMapping("/bulk")
+  public List<OrderResponse> bulkCreate(@RequestBody List<CreateOrderRequest> requests) {
+    return requests.stream()
+        .map(request -> orderService.placeOrder(mapper.toCommand(request)))
+        .map(mapper::toResponse)
+        .toList();
+  }
"""


def build_review_payload() -> dict[str, object]:
    return {
        "subject_type": "mr",
        "analysis_mode": "standard",
        "repo_id": "frontend-smoke-order-service",
        "project_id": "acme-shop",
        "source_ref": "feature/payment-reservation",
        "target_ref": "main",
        "title": "订单创建链路新增支付预占",
        "repo_url": "https://example.com/acme/shop.git",
        "mr_url": "https://example.com/acme/shop/merge_requests/42",
        "changed_files": [
            "src/main/java/com/acme/order/OrderService.java",
            "src/main/java/com/acme/order/OrderController.java",
            "src/test/java/com/acme/order/OrderServiceTest.java",
        ],
        "unified_diff": build_demo_diff(),
        "selected_experts": ["java_backend", "test_engineer", "security_reviewer", "change_impact_analysis"],
        "metadata": {
            "impact_report": build_impact_report(),
            "impact_analysis_progress": {
                "state": "completed",
                "graph_status": "ready",
                "message": "GitNexus 影响报告由前端演示用例数据生成。",
            },
            "review_policy": {
                "max_comments_per_review": 5,
                "excluded_changed_files": ["src/test/java/com/acme/order/OrderServiceTest.java"],
                "reviewable_changed_files": [
                    "src/main/java/com/acme/order/OrderService.java",
                    "src/main/java/com/acme/order/OrderController.java",
                ],
                "required_experts": ["java_backend", "test_engineer", "change_impact_analysis"],
                "path_rules": [{"pattern": "src/main/java/**", "experts": ["java_backend"]}],
            },
            "manual_expert_selection": True,
            "allow_empty_diff_fallback": False,
            "smoke_requires_llm": True,
        },
    }


def build_impact_report() -> dict[str, object]:
    return {
        "graph_status": "ready",
        "graph_indexed_at": "2026-05-01T15:10:00Z",
        "graph_commit": "7f3a9c2",
        "fact_source": "gitnexus_mcp",
        "analysis_workflow": [
            "统一 POSIX 与 Windows 路径格式",
            "查询变更符号的上下文",
            "分析变更符号的影响范围",
            "按影响路径和风险排序测试范围",
        ],
        "changed_files": [
            "src/main/java/com/acme/order/OrderService.java",
            "src/main/java/com/acme/order/OrderController.java",
            "src/test/java/com/acme/order/OrderServiceTest.java",
        ],
        "changed_symbols": [
            {
                "file_path": "src/main/java/com/acme/order/OrderService.java",
                "symbol": "OrderService.placeOrder",
                "kind": "method",
                "container": "OrderService",
                "line_start": 42,
            },
            {
                "file_path": "src/main/java/com/acme/order/OrderController.java",
                "symbol": "OrderController.create",
                "kind": "method",
                "container": "OrderController",
                "line_start": 31,
            },
        ],
        "impacted_files": [
            {
                "file_path": "src/main/java/com/acme/payment/PaymentClient.java",
                "relationship": "outbound_call",
                "reason": "OrderService.placeOrder 在保存订单前新增调用 PaymentClient.reserve。",
                "risk_level": "high",
            },
            {
                "file_path": "src/main/java/com/acme/inventory/InventoryService.java",
                "relationship": "transactional_dependency",
                "reason": "库存预留与支付预占在失败回滚时必须保持一致。",
                "risk_level": "high",
            },
            {
                "file_path": "src/main/java/com/acme/notification/OrderEventPublisher.java",
                "relationship": "domain_event_consumer",
                "reason": "支付预占失败后，OrderCreated 事件发布时间会影响下游一致性。",
                "risk_level": "medium",
            },
            {
                "file_path": "src/main/java/com/acme/risk/FraudRiskClient.java",
                "relationship": "outbound_call",
                "reason": "OrderController 批量创建路径会重复调用订单服务，可能放大下游风控检查。",
                "risk_level": "medium",
            },
        ],
        "impacted_modules": ["order", "payment", "inventory", "notification", "risk"],
        "impact_paths": [
            {
                "source": "OrderController.create",
                "target": "PaymentClient.reserve",
                "path": ["OrderController.create", "OrderService.placeOrder", "PaymentClient.reserve"],
                "depth": 2,
                "risk": "high",
            },
            {
                "source": "OrderService.placeOrder",
                "target": "InventoryService.reserve",
                "path": ["OrderService.placeOrder", "InventoryService.reserve"],
                "depth": 1,
                "risk": "high",
            },
            {
                "source": "OrderService.placeOrder",
                "target": "OrderEventPublisher.publishCreated",
                "path": ["OrderService.placeOrder", "OrderEventPublisher.publishCreated"],
                "depth": 1,
                "risk": "medium",
            },
            {
                "source": "OrderController.bulkCreate",
                "target": "PaymentClient.reserve",
                "path": ["OrderController.bulkCreate", "OrderService.placeOrder", "PaymentClient.reserve"],
                "depth": 2,
                "risk": "high",
            },
        ],
        "impact_graph": {
            "nodes": [
                {
                    "node_id": "controller_create",
                    "label": "OrderController.create",
                    "kind": "method",
                    "file_path": "src/main/java/com/acme/order/OrderController.java",
                    "role": "entrypoint",
                    "risk": "medium",
                },
                {
                    "node_id": "service_place_order",
                    "label": "OrderService.placeOrder",
                    "kind": "method",
                    "file_path": "src/main/java/com/acme/order/OrderService.java",
                    "role": "changed",
                    "risk": "high",
                },
                {
                    "node_id": "payment_reserve",
                    "label": "PaymentClient.reserve",
                    "kind": "method",
                    "file_path": "src/main/java/com/acme/payment/PaymentClient.java",
                    "role": "impacted",
                    "risk": "high",
                },
                {
                    "node_id": "inventory_reserve",
                    "label": "InventoryService.reserve",
                    "kind": "method",
                    "file_path": "src/main/java/com/acme/inventory/InventoryService.java",
                    "role": "impacted",
                    "risk": "high",
                },
                {
                    "node_id": "event_publish",
                    "label": "OrderEventPublisher.publishCreated",
                    "kind": "method",
                    "file_path": "src/main/java/com/acme/notification/OrderEventPublisher.java",
                    "role": "impacted",
                    "risk": "medium",
                },
                {
                    "node_id": "controller_bulk_create",
                    "label": "OrderController.bulkCreate",
                    "kind": "method",
                    "file_path": "src/main/java/com/acme/order/OrderController.java",
                    "role": "entrypoint",
                    "risk": "high",
                },
            ],
            "edges": [
                {"source": "controller_create", "target": "service_place_order", "relationship": "calls", "confidence": 0.98},
                {"source": "service_place_order", "target": "payment_reserve", "relationship": "calls", "confidence": 0.93},
                {"source": "service_place_order", "target": "inventory_reserve", "relationship": "calls", "confidence": 0.91},
                {"source": "service_place_order", "target": "event_publish", "relationship": "publishes", "confidence": 0.87},
                {"source": "controller_bulk_create", "target": "service_place_order", "relationship": "loop_calls", "confidence": 0.89},
            ],
        },
        "external_entrypoints": ["POST /api/orders", "POST /api/orders/bulk"],
        "risk_level": "high",
        "recommended_test_scope": [
            {
                "scope": "OrderService 事务回滚测试",
                "reason": "支付预占失败时必须释放库存，并避免发布 OrderCreated 事件。",
                "paths": ["OrderService.placeOrder -> PaymentClient.reserve", "OrderService.placeOrder -> InventoryService.reserve"],
                "priority": "high",
            },
            {
                "scope": "订单 API 集成测试",
                "reason": "POST /api/orders 现在依赖支付预占的延迟和失败语义。",
                "paths": ["OrderController.create -> OrderService.placeOrder -> PaymentClient.reserve"],
                "priority": "high",
            },
            {
                "scope": "批量订单负载和限流测试",
                "reason": "POST /api/orders/bulk 会放大下游支付和库存调用。",
                "paths": ["OrderController.bulkCreate -> OrderService.placeOrder -> PaymentClient.reserve"],
                "priority": "high",
            },
            {
                "scope": "通知事件契约测试",
                "reason": "OrderCreated 事件应只在库存和支付都预留成功后发布。",
                "paths": ["OrderService.placeOrder -> OrderEventPublisher.publishCreated"],
                "priority": "medium",
            },
        ],
        "must_run_tests": [
            "OrderServiceTest",
            "OrderControllerIT",
            "BulkOrderControllerIT",
            "PaymentReservationContractTest",
            "InventoryRollbackIT",
        ],
        "queried_targets": [
            "src/main/java/com/acme/order/OrderService.java#OrderService.placeOrder",
            "src/main/java/com/acme/order/OrderController.java#OrderController.create",
            "src/test/java/com/acme/order/OrderServiceTest.java#OrderServiceTest",
        ],
        "successful_context_targets": [
            "src/main/java/com/acme/order/OrderService.java#OrderService.placeOrder",
            "src/main/java/com/acme/order/OrderController.java#OrderController.create",
        ],
        "successful_impact_targets": [
            "src/main/java/com/acme/order/OrderService.java#OrderService.placeOrder",
            "src/main/java/com/acme/order/OrderController.java#OrderController.create",
        ],
        "skipped_invalid_targets": [],
        "skipped_missing_context_targets": [
            "src/test/java/com/acme/order/OrderServiceTest.java#OrderServiceTest",
        ],
        "skipped_missing_impact_targets": [],
        "manual_verification": [
            "确认 PaymentClient.reserve 在重试场景下具备幂等保护。",
            "确认支付预占超时时会执行库存释放。",
        ],
        "limitations": [
            "GitNexus 图谱未索引测试符号，因此测试目标被跳过，符合预期。",
        ],
        "report_summary": "本次订单创建链路新增支付预占，GitNexus 识别到支付、库存和通知三个高相关影响面，建议优先验证事务回滚和 API 集成路径。",
        "key_impact_points": [
            "OrderService.placeOrder 是主要变更符号，向 payment 与 inventory 扩散。",
            "POST /api/orders 的失败语义从单模块校验扩展为跨模块事务一致性。",
            "OrderCreated 事件必须延后到支付与库存都成功后再发布。",
            "批量创建入口会放大下游 payment/inventory 调用，需要限流或批量化验证。",
        ],
        "test_focus": [
            "支付预占失败时不应创建订单事件。",
            "库存预留成功但支付超时时必须释放库存。",
            "订单 API 应返回稳定错误码并保留审计日志。",
            "批量订单接口应避免逐条远程调用和敏感支付信息日志。",
        ],
        "llm_generated": False,
    }


def _seed_static_snapshot() -> str:
    service = ReviewService()
    now = datetime.now(UTC)
    review = service.create_review(build_review_payload())

    id_suffix = review.review_id.replace("rev_", "")
    direct_finding_id = f"fdg_frontend_smoke_payment_{id_suffix}"
    nplus1_finding_id = f"fdg_frontend_smoke_bulk_loop_{id_suffix}"
    security_finding_id = f"fdg_frontend_smoke_pii_log_{id_suffix}"
    noise_finding_id = f"fdg_frontend_smoke_noise_{id_suffix}"
    issue_id = f"iss_frontend_smoke_payment_{id_suffix}"
    nplus1_issue_id = f"iss_frontend_smoke_bulk_loop_{id_suffix}"
    security_issue_id = f"iss_frontend_smoke_pii_log_{id_suffix}"
    rejected_issue_id = f"iss_frontend_smoke_rejected_{id_suffix}"

    finding_direct = ReviewFinding(
        finding_id=direct_finding_id,
        review_id=review.review_id,
        expert_id="java_backend",
        title="支付预占失败后缺少库存释放补偿",
        summary="新增 PaymentClient.reserve 后，库存已预留但支付超时会中断订单创建，当前 diff 未展示释放库存或事务补偿逻辑。",
        finding_type="direct_defect",
        normalized_issue_type="transaction_consistency",
        severity="high",
        confidence=0.92,
        file_path="src/main/java/com/acme/order/OrderService.java",
        line_start=42,
        evidence=[
            "OrderService.placeOrder 先调用 inventoryService.reserve，再调用 paymentClient.reserve。",
            "diff 新增了 paymentClient.reserve，但没有新增回滚或补偿处理。",
        ],
        cross_file_evidence=[
            "GitNexus 标记 PaymentClient.reserve 为出站依赖。",
            "InventoryService.reserve 位于同一影响路径上，需要保持事务一致性。",
        ],
        context_files=[
            "src/main/java/com/acme/payment/PaymentClient.java",
            "src/main/java/com/acme/inventory/InventoryService.java",
        ],
        remediation_strategy="将库存预留和支付预占纳入同一事务边界，或在支付失败时增加显式补偿。",
        remediation_suggestion="新增支付失败分支，释放已预留库存，并用测试覆盖超时和重试行为。",
        remediation_steps=[
            "捕获支付预占失败，并释放对应库存预留。",
            "只在库存和支付都成功后发布 OrderCreated 事件。",
            "补充支付超时和重复重试下的回滚测试。",
        ],
        code_excerpt="InventoryReservation inventory = inventoryService.reserve(command.items());\nPaymentReservation payment = paymentClient.reserve(command.payment());",
    )
    finding_noise = ReviewFinding(
        finding_id=noise_finding_id,
        review_id=review.review_id,
        expert_id="style_reviewer",
        title="测试方法命名可以更具体",
        summary="OrderServiceTest 中新增测试名称略泛化，但不影响生产行为，低于本次问题提交上限规则。",
        finding_type="style",
        normalized_issue_type="test_naming",
        severity="low",
        confidence=0.44,
        file_path="src/test/java/com/acme/order/OrderServiceTest.java",
        line_start=18,
        evidence=["测试方法名称较泛化。"],
    )
    finding_nplus1 = ReviewFinding(
        finding_id=nplus1_finding_id,
        review_id=review.review_id,
        expert_id="performance_reliability",
        title="批量创建接口逐条调用订单服务导致下游调用放大",
        summary="新增 bulkCreate 对每个请求逐条调用 orderService.placeOrder，会把 PaymentClient 和 InventoryService 的远程调用按请求数线性放大。",
        finding_type="direct_defect",
        normalized_issue_type="loop_call_amplification",
        severity="high",
        confidence=0.88,
        file_path="src/main/java/com/acme/order/OrderController.java",
        line_start=34,
        evidence=[
            "bulkCreate 使用 requests.stream().map(...) 逐条调用 orderService.placeOrder。",
            "placeOrder 会继续调用 PaymentClient.reserve 和 InventoryService.reserve。",
        ],
        cross_file_evidence=[
            "GitNexus 路径显示：OrderController.bulkCreate -> OrderService.placeOrder -> PaymentClient.reserve。",
            "GitNexus 将 payment 和 inventory 模块标记为高风险影响模块。",
        ],
        context_files=[
            "src/main/java/com/acme/order/OrderService.java",
            "src/main/java/com/acme/payment/PaymentClient.java",
        ],
        remediation_strategy="将逐条远程编排改为有边界的批处理流程，或增加明确的限流保护。",
        remediation_suggestion="在开放 POST /api/orders/bulk 前，增加请求数量上限，并优先接入下游批量预留接口。",
        remediation_steps=[
            "增加最大批量数量校验和限流保护。",
            "在下游支持时接入批量支付和库存预留接口。",
            "补充 100 条批量请求的延迟和部分失败测试。",
        ],
        code_excerpt="return requests.stream().map(request -> orderService.placeOrder(mapper.toCommand(request))).toList();",
    )
    finding_security = ReviewFinding(
        finding_id=security_finding_id,
        review_id=review.review_id,
        expert_id="security_compliance",
        title="支付 token 与卡号被写入审计日志",
        summary="新增 auditLogger.info 直接输出 command.payment().token() 和 cardNo()，会把敏感支付凭证写入日志系统。",
        finding_type="direct_defect",
        normalized_issue_type="sensitive_data_logging",
        severity="critical",
        confidence=0.94,
        file_path="src/main/java/com/acme/order/OrderService.java",
        line_start=43,
        evidence=[
            "diff 新增的 auditLogger.info 输出了 command.payment().token() 和 command.payment().cardNo()。",
            "支付 token 和卡号属于敏感支付数据，不能写入日志。",
        ],
        cross_file_evidence=[
            "订单 API 是外部可访问入口。",
            "PaymentClient.reserve 使用同一个 payment 对象。",
        ],
        context_files=[
            "src/main/java/com/acme/order/OrderController.java",
            "src/main/java/com/acme/payment/PaymentClient.java",
        ],
        remediation_strategy="移除日志中的原始支付字段，只记录脱敏后的非敏感标识。",
        remediation_suggestion="仅记录支付预占单号和订单号；token/cardNo 应完全省略或按合规规则脱敏。",
        remediation_steps=[
            "从 auditLogger 参数中删除 token/cardNo。",
            "如需审计关联，新增 PaymentLogSanitizer 做统一脱敏。",
            "增加日志捕获测试，断言敏感字段不会出现在日志中。",
        ],
        code_excerpt='auditLogger.info("payment reserved token={} card={}", command.payment().token(), command.payment().cardNo());',
    )
    service.finding_repo.save_many(review.review_id, [finding_direct, finding_nplus1, finding_security, finding_noise])

    issue = DebateIssue(
        issue_id=issue_id,
        canonical_issue_id=issue_id,
        review_id=review.review_id,
        title=finding_direct.title,
        summary=finding_direct.summary,
        finding_type="direct_defect",
        normalized_issue_type="transaction_consistency",
        primary_expert_id="java_backend",
        aggregated_finding_types=["direct_defect"],
        file_path=finding_direct.file_path,
        line_start=finding_direct.line_start,
        status="open",
        severity="high",
        confidence=0.92,
        confidence_breakdown={
            "evidence_strength": 0.95,
            "cross_file_impact": 0.9,
            "false_positive_risk": 0.1,
        },
        llm_judge_result={
            "final_verdict": "accept",
            "decision": "accepted",
            "confidence": 0.9,
            "reason": "直接 diff 证据和 GitNexus 影响路径共同指向明确的一致性风险。",
        },
        finding_ids=[finding_direct.finding_id],
        participant_expert_ids=["java_backend", "test_engineer", "change_impact_analysis"],
        evidence=finding_direct.evidence,
        cross_file_evidence=finding_direct.cross_file_evidence,
        evidence_chain=[
            {
                "kind": "diff",
                "file_path": finding_direct.file_path,
                "line_start": finding_direct.line_start,
                "summary": "支付预占被插入到库存预留之后。",
            },
            {
                "kind": "gitnexus_path",
                "file_path": "src/main/java/com/acme/payment/PaymentClient.java",
                "summary": "GitNexus 路径：OrderService.placeOrder -> PaymentClient.reserve。",
            },
            {
                "kind": "gitnexus_path",
                "file_path": "src/main/java/com/acme/inventory/InventoryService.java",
                "summary": "GitNexus 路径：OrderService.placeOrder -> InventoryService.reserve。",
            },
        ],
        direct_evidence=True,
        verified=True,
        tool_name="gitnexus_mcp",
        tool_verified=True,
        remediation_strategy=finding_direct.remediation_strategy,
        remediation_suggestion=finding_direct.remediation_suggestion,
        remediation_steps=finding_direct.remediation_steps,
        updated_at=now,
    )
    issue_nplus1 = DebateIssue(
        issue_id=nplus1_issue_id,
        canonical_issue_id=nplus1_issue_id,
        review_id=review.review_id,
        title=finding_nplus1.title,
        summary=finding_nplus1.summary,
        finding_type="direct_defect",
        normalized_issue_type="loop_call_amplification",
        primary_expert_id="performance_reliability",
        aggregated_finding_types=["direct_defect"],
        file_path=finding_nplus1.file_path,
        line_start=finding_nplus1.line_start,
        status="open",
        severity="high",
        confidence=0.88,
        confidence_breakdown={"evidence_strength": 0.88, "cross_file_impact": 0.9, "false_positive_risk": 0.16},
        llm_judge_result={"final_verdict": "accept", "decision": "accepted", "confidence": 0.86, "reason": "diff 中可直接看到循环调用放大，并且该路径连接到高风险下游模块。"},
        finding_ids=[finding_nplus1.finding_id],
        participant_expert_ids=["performance_reliability", "change_impact_analysis", "test_engineer"],
        evidence=finding_nplus1.evidence,
        cross_file_evidence=finding_nplus1.cross_file_evidence,
        evidence_chain=[
            {"kind": "diff", "file_path": finding_nplus1.file_path, "line_start": finding_nplus1.line_start, "summary": "bulkCreate 将请求逐条传入 placeOrder。"},
            {"kind": "gitnexus_path", "file_path": "src/main/java/com/acme/payment/PaymentClient.java", "summary": "批量入口会经由 OrderService 触达 PaymentClient.reserve。"},
        ],
        direct_evidence=True,
        verified=True,
        tool_name="gitnexus_mcp",
        tool_verified=True,
        remediation_strategy=finding_nplus1.remediation_strategy,
        remediation_suggestion=finding_nplus1.remediation_suggestion,
        remediation_steps=finding_nplus1.remediation_steps,
        updated_at=now,
    )
    issue_security = DebateIssue(
        issue_id=security_issue_id,
        canonical_issue_id=security_issue_id,
        review_id=review.review_id,
        title=finding_security.title,
        summary=finding_security.summary,
        finding_type="direct_defect",
        normalized_issue_type="sensitive_data_logging",
        primary_expert_id="security_compliance",
        aggregated_finding_types=["direct_defect"],
        file_path=finding_security.file_path,
        line_start=finding_security.line_start,
        status="needs_human",
        severity="critical",
        confidence=0.94,
        confidence_breakdown={"evidence_strength": 0.96, "cross_file_impact": 0.82, "false_positive_risk": 0.05},
        llm_judge_result={"final_verdict": "accept", "decision": "accepted", "confidence": 0.92, "reason": "新增日志语句中直接出现了敏感支付字段。"},
        finding_ids=[finding_security.finding_id],
        participant_expert_ids=["security_compliance", "java_backend", "change_impact_analysis"],
        evidence=finding_security.evidence,
        cross_file_evidence=finding_security.cross_file_evidence,
        evidence_chain=[
            {"kind": "diff", "file_path": finding_security.file_path, "line_start": finding_security.line_start, "summary": "新增日志语句包含 token 和卡号。"},
            {"kind": "entrypoint", "file_path": "src/main/java/com/acme/order/OrderController.java", "summary": "外部订单 API 传入 payment 对象。"},
        ],
        direct_evidence=True,
        verified=True,
        needs_human=True,
        tool_name="static_diff_review",
        tool_verified=True,
        remediation_strategy=finding_security.remediation_strategy,
        remediation_suggestion=finding_security.remediation_suggestion,
        remediation_steps=finding_security.remediation_steps,
        updated_at=now,
    )
    rejected_issue = DebateIssue(
        issue_id=rejected_issue_id,
        canonical_issue_id=rejected_issue_id,
        review_id=review.review_id,
        title="已人工驳回的测试命名问题",
        summary="人工确认测试命名建议不构成本次正式修复议题。",
        finding_type="style",
        normalized_issue_type="test_naming",
        primary_expert_id="style_reviewer",
        aggregated_finding_types=["style"],
        file_path=finding_noise.file_path,
        line_start=finding_noise.line_start,
        status="resolved",
        severity="low",
        confidence=0.44,
        finding_ids=[finding_noise.finding_id],
        participant_expert_ids=["style_reviewer"],
        human_decision="rejected",
        resolution="human_rejected",
        updated_at=now,
    )
    service.issue_repo.save_all(review.review_id, [issue, issue_nplus1, issue_security, rejected_issue])

    service.message_repo.append_many(
        [
            ConversationMessage(
                review_id=review.review_id,
                issue_id="",
                expert_id="change_impact_analysis",
                message_type="impact_report_generated",
                content="GitNexus 已生成订单链路关联影响报告。",
                created_at=now - timedelta(seconds=50),
                metadata={"impact_report": build_impact_report(), "graph_status": "ready", "risk_level": "high"},
            ),
            ConversationMessage(
                review_id=review.review_id,
                issue_id=issue.issue_id,
                expert_id="judge",
                message_type="issue_filter_applied",
                content="该问题有直接 diff 证据、跨文件证据和 GitNexus 调用链支撑，保留为高优先级问题。",
                created_at=now - timedelta(seconds=20),
                metadata={
                    "issue_filter_decisions": [
                        {
                            "topic": finding_noise.title,
                            "rule_code": "low_confidence_noise",
                            "rule_label": "低置信噪声过滤",
                            "reason": "低置信度样式建议，且命中文件已被仓库策略排除。",
                            "severity": "low",
                            "finding_ids": [finding_noise.finding_id],
                            "finding_titles": [finding_noise.title],
                            "expert_ids": [finding_noise.expert_id],
                        },
                        {
                            "topic": "已人工驳回的测试命名问题",
                            "rule_code": "human_rejected",
                            "rule_label": "人工驳回",
                            "reason": "人工确认该项不进入正式问题清单，仅保留反馈学习记录。",
                            "severity": "low",
                            "finding_ids": [finding_noise.finding_id],
                            "finding_titles": [finding_noise.title],
                            "expert_ids": [finding_noise.expert_id],
                        },
                        {
                            "topic": "问题提交上限控制",
                            "rule_code": "repo_policy_comment_budget",
                            "rule_label": "仓库提交上限",
                            "reason": "本次仅保留有直接证据链和高影响路径的问题。",
                            "severity": "info",
                            "finding_ids": [],
                            "finding_titles": [],
                            "expert_ids": ["judge"],
                        },
                    ]
                },
            ),
        ]
    )
    service.event_repo.append_many(
        [
            ReviewEvent(
                review_id=review.review_id,
                event_type="impact_report_generated",
                phase="impact_analysis",
                message="GitNexus impact report generated.",
                created_at=now - timedelta(seconds=55),
                payload={"risk_level": "high", "graph_status": "ready"},
            ),
            ReviewEvent(
                review_id=review.review_id,
                event_type="review_completed",
                phase="completed",
                message="Frontend quality and impact smoke review completed.",
                created_at=now,
            ),
        ]
    )

    review.status = "waiting_human"
    review.phase = "human_gate"
    review.started_at = now - timedelta(minutes=3)
    review.completed_at = None
    review.duration_seconds = 180.0
    review.human_review_status = "requested"
    review.pending_human_issue_ids = [issue_security.issue_id]
    review.report_summary = "识别 3 个高优先级 Java 问题，过滤 1 条低置信噪声，另有 1 条人工驳回项不进入正式问题清单；GitNexus 影响范围覆盖 payment、inventory、notification、risk。"
    review.updated_at = now
    service.review_repo.save(review)
    service.artifact_service.publish(review, [issue, issue_nplus1, issue_security])
    return review.review_id


def seed_with_llm() -> str:
    service = ReviewService()
    review = service.create_review(build_review_payload())
    review = service.start_review(review.review_id)
    report = service.build_report(review.review_id)
    live_llm_calls = sum(
        1
        for message in service.message_repo.list(review.review_id)
        if str((message.metadata or {}).get("mode") or "").strip().lower() == "live"
        and str((message.metadata or {}).get("llm_call_id") or "").strip()
    )
    if review.status == "failed":
        raise RuntimeError(review.failure_reason or f"review failed: {review.review_id}")
    if int(report.llm_usage_summary.total_calls or 0) <= 0 or live_llm_calls <= 0:
        raise RuntimeError(f"smoke review must use LLM, got zero live calls: {review.review_id}")
    return review.review_id


def seed() -> str:
    return seed_with_llm()


if __name__ == "__main__":
    review_id = seed()
    print(review_id)
    print(f"http://127.0.0.1:5174/review/{review_id}?tab=result")
    print(f"http://127.0.0.1:5174/review/{review_id}?tab=impact")
