from app.services.java_quality_signal_extractor import JavaQualitySignalExtractor


def test_java_quality_signal_extractor_detects_general_java_quality_signals() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -20,7 +20,7 @@ public class MySqlDomainEventsConsumer {",
                    "-\tprivate final Integer CHUNKS = 200;",
                    "+\tprivate final Integer chunksTmp = 200;",
                    "@@ -37,11 +37,9 @@ public class MySqlDomainEventsConsumer {",
                    '-\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk"',
                    '+\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC"',
                    '-\t\t\tquery.setParameter("chunk", CHUNKS);',
                    "@@ -56,7 +54,6 @@ public class MySqlDomainEventsConsumer {",
                    "-\t\t\t\te.printStackTrace();",
                ]
            )
        },
        repository_context={
            "current_class_context": {
                "snippet": "\n".join(
                    [
                        "35 | \t@Transactional",
                        "36 | \tpublic void consume() {",
                        "37 | \t\ttry {",
                        "38 | \t\t} catch (Exception e) {",
                        "39 | \t\t}",
                    ]
                )
            }
        },
    )

    assert "unbounded_query_risk" in payload["signals"]
    assert "naming_convention_violation" in payload["signals"]
    assert "exception_swallowed" in payload["signals"]


def test_java_quality_signal_extractor_detects_factory_bypass_and_event_ordering() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -15,9 +15,9 @@ public final class CourseCreator {",
                    "-        Course course = Course.create(id, name, duration);",
                    "+        Course course = new Course(id, name, duration);",
                    "-        repository.save(course);",
                    "         eventBus.publish(course.pullDomainEvents());",
                    "+        repository.save(course);",
                ]
            )
        },
    )

    assert "factory_bypass" in payload["signals"]
    assert "event_ordering_risk" in payload["signals"]


def test_java_quality_signal_extractor_detects_magic_value_and_weak_naming() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -40,4 +40,8 @@ public class OrderService {",
                    '+    String orderStatusTmp = "MANUAL_RETRY";',
                    "+    if (retryCount > 37) {",
                    "+        return processWithPriority(orderStatusTmp, 86400);",
                    "+    }",
                ]
            )
        },
    )

    assert "naming_convention_violation" in payload["signals"]
    assert "magic_value_literal" in payload["signals"]


def test_java_quality_signal_extractor_detects_loop_call_amplification() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderBatchService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -40,6 +40,10 @@ public class OrderBatchService {",
                    "+    for (OrderItem item : items) {",
                    "+        OrderEntity entity = orderRepository.findByOrderNo(item.getOrderNo());",
                    "+        remoteInventoryClient.reserve(item.getSku(), item.getQuantity());",
                    "+    }",
                ]
            )
        },
    )

    assert "loop_call_amplification" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "loop_call_amplification")
    assert observation["kind"] == "control_flow_with_external_call"
    assert observation["line_start"] == 40
    assert "批量路径放大" in observation["risk_hints"]


def test_java_quality_signal_extractor_detects_loop_call_amplification_from_context() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderBatchService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -40,2 +40,4 @@ public class OrderBatchService {",
                    "+    processOrders(items);",
                    "+    return;",
                ]
            )
        },
        repository_context={
            "current_class_context": {
                "snippet": "\n".join(
                    [
                        "40 |     private void processOrders(List<OrderItem> items) {",
                        "41 |         for (OrderItem item : items) {",
                        "42 |             orderRepository.findByOrderNo(item.getOrderNo());",
                        "43 |             remoteInventoryClient.reserve(item.getSku(), item.getQuantity());",
                        "44 |         }",
                        "45 |     }",
                    ]
                )
            }
        },
    )

    assert "loop_call_amplification" in payload["signals"]


def test_java_quality_signal_extractor_detects_stream_foreach_loop_call_amplification() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderBatchService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -40,2 +40,6 @@ public class OrderBatchService {",
                    "+    items.stream().forEach(item -> {",
                    "+        orderService.process(item);",
                    "+        remotePriceClient.fetch(item.getSku());",
                    "+    });",
                ]
            )
        },
    )

    assert "loop_call_amplification" in payload["signals"]


def test_java_quality_signal_extractor_detects_loop_call_amplification_for_service_call() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderBatchService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -40,2 +40,5 @@ public class OrderBatchService {",
                    "+    for (OrderItem item : items) {",
                    "+        pricingService.calculate(item);",
                    "+    }",
                ]
            )
        },
    )

    assert "loop_call_amplification" in payload["signals"]


def test_java_quality_signal_extractor_detects_loop_call_amplification_for_method_reference() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderBatchService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -40,2 +40,4 @@ public class OrderBatchService {",
                    "+    items.forEach(notificationService::send);",
                ]
            )
        },
    )

    assert "loop_call_amplification" in payload["signals"]


def test_java_quality_signal_extractor_detects_comment_contract_unimplemented() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -22,4 +22,6 @@ public class OrderService {",
                    "+    // TODO: 创建订单后自动扣减库存并发送事件",
                    "+    public Order create(Order order) {",
                    "+        return orderRepository.save(order);",
                    "+    }",
                ]
            )
        },
    )

    assert "comment_contract_unimplemented" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "comment_contract_unimplemented")
    assert observation["kind"] == "declared_intent_without_implementation"
    assert observation["line_start"] == 22
    assert "承诺未落地" in observation["risk_hints"]


def test_java_quality_signal_extractor_detects_comment_contract_unimplemented_from_context() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -22,1 +22,2 @@ public class OrderService {",
                    "+    return createOrder(order);",
                ]
            )
        },
        repository_context={
            "current_class_context": {
                "snippet": "\n".join(
                    [
                        "22 |     // TODO: 创建订单后自动扣减库存并发送事件",
                        "23 |     public Order createOrder(Order order) {",
                        "24 |         return orderRepository.save(order);",
                        "25 |     }",
                    ]
                )
            }
        },
    )

    assert "comment_contract_unimplemented" in payload["signals"]


def test_java_quality_signal_extractor_detects_cross_layer_dependency() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/order/interfaces/OrderController.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -18,2 +18,5 @@ public class OrderController {",
                    "+    public OrderVO create(CreateOrderRequest request) {",
                    "+        OrderEntity entity = orderRepository.save(mapper.toEntity(request));",
                    "+        return mapper.toVO(entity);",
                    "+    }",
                ]
            )
        },
        repository_context={
            "current_class_context": {
                "snippet": "\n".join(
                    [
                        "18 | @RestController",
                        "19 | public class OrderController {",
                        "20 |     private final OrderRepository orderRepository;",
                    ]
                )
            }
        },
    )

    assert "cross_layer_dependency" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "cross_layer_dependency")
    assert observation["kind"] == "cross_layer_dependency"
    assert observation["language"] == "java"


def test_java_quality_signal_extractor_detects_transactional_side_effect() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/order/application/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -30,2 +30,7 @@ public class OrderService {",
                    "+    @Transactional",
                    "+    public void create(Order order) {",
                    "+        orderRepository.save(order);",
                    "+        remoteInventoryClient.reserve(order.getSku(), order.getQuantity());",
                    "+        eventBus.publish(order.pullDomainEvents());",
                    "+    }",
                ]
            )
        },
    )

    assert "transactional_side_effect" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "transactional_side_effect")
    assert observation["kind"] == "transactional_side_effect"
    assert "事务内副作用" in observation["risk_hints"][0]


def test_java_quality_signal_extractor_detects_exception_semantics_weakened() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/order/application/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -44,2 +44,8 @@ public class OrderService {",
                    "+    public boolean reserve(Order order) {",
                    "+        try {",
                    "+            remoteInventoryClient.reserve(order.getSku(), order.getQuantity());",
                    "+        } catch (Exception ex) {",
                    "+            return true;",
                    "+        }",
                    "+    }",
                ]
            )
        },
    )

    assert "exception_semantics_weakened" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "exception_semantics_weakened")
    assert observation["kind"] == "error_semantics_changed"


def test_java_quality_signal_extractor_detects_configuration_behavior_coupling() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/order/application/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -18,2 +18,7 @@ public class OrderService {",
                    '+    @Value("${order.reserve.enabled}")',
                    "+    private boolean reserveEnabled;",
                    "+    public void create(Order order) {",
                    "+        if (reserveEnabled) {",
                    "+            remoteInventoryClient.reserve(order.getSku(), order.getQuantity());",
                    "+        }",
                    "+    }",
                ]
            )
        },
    )

    assert "configuration_behavior_coupling" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "configuration_behavior_coupling")
    assert observation["kind"] == "configuration_behavior_coupling"


def test_java_quality_signal_extractor_detects_bulk_processing_risk() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/order/application/OrderBatchService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -22,2 +22,8 @@ public class OrderBatchService {",
                    "+    public void syncAll(List<Order> orders) {",
                    "+        orders.forEach(order -> {",
                    "+            orderRepository.save(order);",
                    "+            notificationClient.send(order.getId());",
                    "+        });",
                    "+    }",
                ]
            )
        },
    )

    assert "bulk_processing_risk" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "bulk_processing_risk")
    assert observation["kind"] == "bulk_processing_boundary_missing"


def test_java_quality_signal_extractor_detects_query_plan_risk() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/order/infrastructure/OrderRepositoryImpl.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -28,2 +28,7 @@ public class OrderRepositoryImpl {",
                    '+    String sql = "select * from orders where status like ? order by created_at desc";',
                    "+    return jdbcTemplate.query(sql, rowMapper, keyword + \"%\");",
                ]
            )
        },
    )

    assert "query_plan_risk" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "query_plan_risk")
    assert observation["kind"] == "query_plan_risk"


def test_java_quality_signal_extractor_detects_comment_only_empty_method() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -22,1 +22,5 @@ public class OrderService {",
                    "+    public void syncInventory(Order order) {",
                    "+        // TODO: 扣减库存并记录审计日志",
                    "+    }",
                ]
            )
        },
    )

    assert "comment_contract_unimplemented" in payload["signals"]


def test_java_quality_signal_extractor_detects_unsupported_operation_placeholder() -> None:
    extractor = JavaQualitySignalExtractor()
    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -22,1 +22,5 @@ public class OrderService {",
                    "+    public void syncInventory(Order order) {",
                    '+        throw new UnsupportedOperationException("TODO implement");',
                    "+    }",
                ]
            )
        },
    )

    assert "comment_contract_unimplemented" in payload["signals"]
