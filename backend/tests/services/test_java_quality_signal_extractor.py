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
