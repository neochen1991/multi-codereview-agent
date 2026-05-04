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


def build_impact_report() -> dict[str, object]:
    return {
        "graph_status": "ready",
        "graph_indexed_at": "2026-05-01T15:10:00Z",
        "graph_commit": "7f3a9c2",
        "fact_source": "gitnexus_mcp",
        "analysis_workflow": [
            "normalize changed_files for POSIX and Windows paths",
            "context(repo, target) for changed symbols",
            "impact(repo, target) for blast radius",
            "rank tests by impact path and risk",
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
                "reason": "OrderService.placeOrder now calls PaymentClient.reserve before persisting the order.",
                "risk_level": "high",
            },
            {
                "file_path": "src/main/java/com/acme/inventory/InventoryService.java",
                "relationship": "transactional_dependency",
                "reason": "Inventory reservation and payment reservation must remain consistent on rollback.",
                "risk_level": "high",
            },
            {
                "file_path": "src/main/java/com/acme/notification/OrderEventPublisher.java",
                "relationship": "domain_event_consumer",
                "reason": "OrderCreated event timing changes when payment reservation fails.",
                "risk_level": "medium",
            },
        ],
        "impacted_modules": ["order", "payment", "inventory", "notification"],
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
            ],
            "edges": [
                {"source": "controller_create", "target": "service_place_order", "relationship": "calls", "confidence": 0.98},
                {"source": "service_place_order", "target": "payment_reserve", "relationship": "calls", "confidence": 0.93},
                {"source": "service_place_order", "target": "inventory_reserve", "relationship": "calls", "confidence": 0.91},
                {"source": "service_place_order", "target": "event_publish", "relationship": "publishes", "confidence": 0.87},
            ],
        },
        "external_entrypoints": ["POST /api/orders"],
        "risk_level": "high",
        "recommended_test_scope": [
            {
                "scope": "OrderService transaction rollback tests",
                "reason": "Payment reserve failure must release inventory and avoid OrderCreated publication.",
                "paths": ["OrderService.placeOrder -> PaymentClient.reserve", "OrderService.placeOrder -> InventoryService.reserve"],
                "priority": "high",
            },
            {
                "scope": "Order API integration tests",
                "reason": "POST /api/orders now depends on payment reservation latency and failure semantics.",
                "paths": ["OrderController.create -> OrderService.placeOrder -> PaymentClient.reserve"],
                "priority": "high",
            },
            {
                "scope": "Notification event contract tests",
                "reason": "OrderCreated event should only be emitted after both inventory and payment are reserved.",
                "paths": ["OrderService.placeOrder -> OrderEventPublisher.publishCreated"],
                "priority": "medium",
            },
        ],
        "must_run_tests": [
            "OrderServiceTest",
            "OrderControllerIT",
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
            "Confirm PaymentClient.reserve is idempotent under retry.",
            "Confirm inventory release runs when payment reservation times out.",
        ],
        "limitations": [
            "GitNexus graph did not index test symbols, so test target was skipped as expected.",
        ],
        "report_summary": "本次订单创建链路新增支付预占，GitNexus 识别到支付、库存和通知三个高相关影响面，建议优先验证事务回滚和 API 集成路径。",
        "key_impact_points": [
            "OrderService.placeOrder 是主要变更符号，向 payment 与 inventory 扩散。",
            "POST /api/orders 的失败语义从单模块校验扩展为跨模块事务一致性。",
            "OrderCreated 事件必须延后到支付与库存都成功后再发布。",
        ],
        "test_focus": [
            "支付预占失败时不应创建订单事件。",
            "库存预留成功但支付超时时必须释放库存。",
            "订单 API 应返回稳定错误码并保留审计日志。",
        ],
        "llm_generated": False,
    }


def seed() -> str:
    service = ReviewService()
    now = datetime.now(UTC)
    diff = """diff --git a/src/main/java/com/acme/order/OrderService.java b/src/main/java/com/acme/order/OrderService.java
@@ -39,6 +39,8 @@ public class OrderService {
   public Order placeOrder(CreateOrderCommand command) {
     InventoryReservation inventory = inventoryService.reserve(command.items());
+    PaymentReservation payment = paymentClient.reserve(command.payment());
+    auditLogger.info("payment reserved {}", payment.id());
     Order order = orderRepository.save(Order.create(command, inventory));
     eventPublisher.publishCreated(order);
     return order;
"""
    review = service.create_review(
        {
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
            "unified_diff": diff,
            "selected_experts": ["java_backend", "test_engineer", "security_reviewer", "change_impact_analysis"],
            "metadata": {
                "impact_report": build_impact_report(),
                "impact_analysis_progress": {
                    "state": "completed",
                    "graph_status": "ready",
                    "message": "GitNexus impact report generated from seeded frontend smoke data.",
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
            },
        }
    )

    id_suffix = review.review_id.replace("rev_", "")
    direct_finding_id = f"fdg_frontend_smoke_payment_{id_suffix}"
    noise_finding_id = f"fdg_frontend_smoke_noise_{id_suffix}"
    issue_id = f"iss_frontend_smoke_payment_{id_suffix}"

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
            "OrderService.placeOrder now calls inventoryService.reserve before paymentClient.reserve.",
            "The diff adds paymentClient.reserve but does not add rollback or compensation handling.",
        ],
        cross_file_evidence=[
            "PaymentClient.reserve is an outbound dependency reported by GitNexus.",
            "InventoryService.reserve is on the same impact path and must remain consistent.",
        ],
        context_files=[
            "src/main/java/com/acme/payment/PaymentClient.java",
            "src/main/java/com/acme/inventory/InventoryService.java",
        ],
        remediation_strategy="Wrap inventory and payment reservation in a transaction boundary or add explicit compensation on payment failure.",
        remediation_suggestion="Add a failure branch that releases the inventory reservation and covers timeout/retry behavior with tests.",
        remediation_steps=[
            "Catch payment reservation failure and release the inventory reservation.",
            "Emit OrderCreated only after inventory and payment both succeed.",
            "Add rollback tests for payment timeout and duplicate retry.",
        ],
        code_excerpt="InventoryReservation inventory = inventoryService.reserve(command.items());\nPaymentReservation payment = paymentClient.reserve(command.payment());",
    )
    finding_noise = ReviewFinding(
        finding_id=noise_finding_id,
        review_id=review.review_id,
        expert_id="style_reviewer",
        title="测试方法命名可以更具体",
        summary="OrderServiceTest 中新增测试名称略泛化，但不影响生产行为，低于本次评论预算阈值。",
        finding_type="style",
        normalized_issue_type="test_naming",
        severity="low",
        confidence=0.44,
        file_path="src/test/java/com/acme/order/OrderServiceTest.java",
        line_start=18,
        evidence=["Test method name is generic."],
    )
    service.finding_repo.save_many(review.review_id, [finding_direct, finding_noise])

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
            "reason": "Direct diff evidence plus GitNexus impact path show a concrete consistency risk.",
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
                "summary": "Payment reserve was inserted after inventory reserve.",
            },
            {
                "kind": "gitnexus_path",
                "file_path": "src/main/java/com/acme/payment/PaymentClient.java",
                "summary": "GitNexus path: OrderService.placeOrder -> PaymentClient.reserve.",
            },
            {
                "kind": "gitnexus_path",
                "file_path": "src/main/java/com/acme/inventory/InventoryService.java",
                "summary": "GitNexus path: OrderService.placeOrder -> InventoryService.reserve.",
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
    service.issue_repo.save_all(review.review_id, [issue])

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
                            "topic": "评论预算控制",
                            "rule_code": "repo_policy_comment_budget",
                            "rule_label": "仓库评论预算",
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

    review.status = "completed"
    review.phase = "completed"
    review.started_at = now - timedelta(minutes=3)
    review.completed_at = now
    review.duration_seconds = 180.0
    review.report_summary = "识别 1 个高优先级事务一致性问题，过滤 1 条低置信噪声，GitNexus 影响范围覆盖 payment、inventory、notification。"
    review.updated_at = now
    service.review_repo.save(review)
    service.artifact_service.publish(review, [issue])
    return review.review_id


if __name__ == "__main__":
    review_id = seed()
    print(review_id)
    print(f"http://127.0.0.1:5174/review/{review_id}?tab=result")
    print(f"http://127.0.0.1:5174/review/{review_id}?tab=impact")
