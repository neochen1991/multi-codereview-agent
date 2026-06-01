from app.services.issue_evidence_anchor_service import IssueEvidenceAnchorService


def test_anchor_validation_passes_current_added_code():
    diff = """
diff --git a/src/main/java/app/OrderController.java b/src/main/java/app/OrderController.java
@@ -10,6 +10,8 @@ public class OrderController {
+    public Order query(String userId) {
+        return orderRepository.findByUserId(userId);
+    }
"""

    result = IssueEvidenceAnchorService().validate_issue(
        {
            "file_path": "src/main/java/app/OrderController.java",
            "line_start": 11,
            "title": "资源归属校验缺失",
            "summary": "使用请求参数 userId 查询订单，未校验当前登录用户与 userId 的关系。",
            "current_code": "11 | +        return orderRepository.findByUserId(userId);",
            "normalized_issue_type": "missing_auth_check",
        },
        changed_files=["src/main/java/app/OrderController.java"],
        unified_diff=diff,
    )

    assert result["status"] == "passed"
    assert result["reason_code"] == "anchored_to_current_diff"


def test_anchor_validation_rejects_deleted_only_code():
    diff = """
diff --git a/src/main/java/app/OrderRepository.java b/src/main/java/app/OrderRepository.java
@@ -10,7 +10,6 @@ public class OrderRepository {
-    List<Order> findAllWithoutLimit();
+    List<Order> findRecent(Pageable pageable);
}
"""

    result = IssueEvidenceAnchorService().validate_issue(
        {
            "file_path": "src/main/java/app/OrderRepository.java",
            "line_start": 10,
            "title": "查询边界缺失",
            "summary": "删除分页限制后可能全表查询。",
            "current_code": "   - |     List<Order> findAllWithoutLimit();",
            "normalized_issue_type": "query_boundary_missing",
        },
        changed_files=["src/main/java/app/OrderRepository.java"],
        unified_diff=diff,
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "deleted_code_only"


def test_anchor_validation_rejects_claim_code_mismatch():
    diff = """
diff --git a/src/main/java/app/OrderService.java b/src/main/java/app/OrderService.java
@@ -20,6 +20,8 @@ public class OrderService {
+    public void save(Order order) {
+        orderRepository.save(order);
+    }
"""

    result = IssueEvidenceAnchorService().validate_issue(
        {
            "file_path": "src/main/java/app/OrderService.java",
            "line_start": 21,
            "title": "异常被吞掉",
            "summary": "catch 分支吞掉异常后仍返回成功。",
            "current_code": "21 | +        orderRepository.save(order);",
            "normalized_issue_type": "exception_swallowed",
        },
        changed_files=["src/main/java/app/OrderService.java"],
        unified_diff=diff,
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "claim_code_mismatch"
