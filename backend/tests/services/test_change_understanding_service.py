from app.services.change_understanding_service import ChangeUnderstandingService


def test_change_understanding_classifies_java_web_transaction_change():
    diff = """
diff --git a/src/main/java/app/order/OrderController.java b/src/main/java/app/order/OrderController.java
@@ -10,6 +10,11 @@ public class OrderController {
+    @PostMapping("/orders")
+    public OrderResponse createOrder(@RequestBody CreateOrderRequest request, @RequestParam String userId) {
+        return orderService.createOrder(userId, request.getAmount(), request.getStatus());
+    }
+}
diff --git a/src/main/java/app/order/OrderService.java b/src/main/java/app/order/OrderService.java
@@ -20,8 +20,15 @@ public class OrderService {
+    @Transactional
+    public void createOrder(String userId, BigDecimal amount, String status) {
+        Order order = orderRepository.findByUserId(userId);
+        eventPublisher.publish(new OrderCreatedEvent(order.getId(), amount));
+    }
diff --git a/src/main/java/app/order/OrderRepository.java b/src/main/java/app/order/OrderRepository.java
@@ -8,4 +8,6 @@ public interface OrderRepository {
+    @Query("select o from Order o where o.userId = :userId")
+    Order findByUserId(String userId);
"""

    result = ChangeUnderstandingService().understand(
        changed_files=[
            "src/main/java/app/order/OrderController.java",
            "src/main/java/app/order/OrderService.java",
            "src/main/java/app/order/OrderRepository.java",
        ],
        unified_diff=diff,
    )

    assert result["risk_domains"] == ["security", "business", "ddd", "database", "transaction", "mq"]
    assert "security_compliance" in result["expert_hints"]
    assert "correctness_business" in result["expert_hints"]
    assert "database_analysis" in result["expert_hints"]
    assert "ddd_architecture" in result["expert_hints"]
    assert "performance_reliability" in result["expert_hints"]
    assert "mq_analysis" in result["expert_hints"]
    assert "userId" in result["changed_symbols"]
    assert "amount" in result["changed_symbols"]

    files = {item["path"]: item for item in result["files"]}
    assert files["src/main/java/app/order/OrderController.java"]["file_role"] == "controller"
    assert files["src/main/java/app/order/OrderService.java"]["file_role"] == "service"
    assert files["src/main/java/app/order/OrderRepository.java"]["file_role"] == "repository"
    assert "createOrder" in files["src/main/java/app/order/OrderService.java"]["changed_methods"]


def test_change_understanding_detects_performance_cache_and_test_context():
    diff = """
diff --git a/src/main/java/app/cache/InventoryCacheService.java b/src/main/java/app/cache/InventoryCacheService.java
@@ -5,6 +5,12 @@ public class InventoryCacheService {
+    public void refreshAll(List<String> skuIds) {
+        for (String skuId : skuIds) {
+            inventoryRepository.findBySkuId(skuId);
+            redisTemplate.opsForValue().set(skuId, "1");
+        }
+    }
diff --git a/src/test/java/app/cache/InventoryCacheServiceTest.java b/src/test/java/app/cache/InventoryCacheServiceTest.java
@@ -1,3 +1,4 @@
+class InventoryCacheServiceTest {}
"""

    result = ChangeUnderstandingService().understand(
        changed_files=[
            "src/main/java/app/cache/InventoryCacheService.java",
            "src/test/java/app/cache/InventoryCacheServiceTest.java",
        ],
        unified_diff=diff,
    )

    assert "performance" in result["risk_domains"]
    assert "cache" in result["risk_domains"]
    assert "test" in result["risk_domains"]
    assert "performance_reliability" in result["expert_hints"]
    assert "redis_analysis" in result["expert_hints"]
    assert "test_verification" in result["expert_hints"]

    files = {item["path"]: item for item in result["files"]}
    assert files["src/main/java/app/cache/InventoryCacheService.java"]["file_role"] == "cache"
    assert files["src/test/java/app/cache/InventoryCacheServiceTest.java"]["is_test"] is True
