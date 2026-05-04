from app.services.code_observation_extractor import CodeObservationExtractor


def test_code_observation_extractor_routes_java_files() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="src/main/java/com/example/OrderService.java",
        target_hunk={
            "excerpt": "\n".join(
                [
                    "@@ -20,2 +20,4 @@ public class OrderService {",
                    "+    @Transactional",
                    "+    public void create(Order order) {",
                    "+        remoteInventoryClient.reserve(order.getSku(), order.getQuantity());",
                    "+    }",
                ]
            )
        },
    )

    assert payload["language"] == "java"
    assert isinstance(payload["observations"], list)


def test_code_observation_extractor_returns_empty_shape_for_unsupported_language() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="src/main/resources/application.yaml",
        target_hunk={"excerpt": "feature.enabled: true"},
    )

    assert payload["language"] == "config"
    assert payload["signals"] == []
    assert payload["summary"] == ""
    assert payload["matched_terms"] == []
    assert payload["signal_terms"] == {}
    assert payload["observations"] == []
    assert payload["analysis_stages"]["rule_stage"]


def test_code_observation_extractor_detects_typescript_comment_contract_gap() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="frontend/src/order/submitOrder.ts",
        target_hunk={
            "start_line": 12,
            "changed_lines": [12, 13],
            "excerpt": "\n".join(
                [
                    "@@ -12,0 +12,2 @@",
                    "+// 创建订单后发送通知",
                    "+return orderRepository.save(order);",
                ]
            ),
        },
    )

    assert payload["language"] == "typescript"
    assert "comment_contract_unimplemented" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "comment_contract_unimplemented")
    assert observation["kind"] == "declared_intent_without_implementation"
    assert observation["line_start"] == 12


def test_code_observation_extractor_detects_python_swallowed_exception() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="backend/app/jobs/payment_job.py",
        target_hunk={
            "start_line": 30,
            "changed_lines": [30, 31],
            "excerpt": "\n".join(
                [
                    "@@ -30,0 +30,3 @@",
                    "+except Exception:",
                    "+    pass",
                    "+return None",
                ]
            ),
        },
    )

    assert payload["language"] == "python"
    assert "exception_swallowed" in payload["signals"]
    assert "exception_semantics_weakened" in payload["signals"]
    assert any(item["kind"] == "error_handling_weakened" for item in payload["observations"])


def test_code_observation_extractor_detects_java_empty_catch_after_removed_handling() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        target_hunk={
            "start_line": 54,
            "changed_lines": [54, 55, 56],
            "excerpt": "\n".join(
                [
                    "@@ -56,7 +54,6 @@ public class MySqlDomainEventsConsumer {",
                    "+            } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException |",
                    "+                     InstantiationException e) {",
                    "-                e.printStackTrace();",
                    "+            }",
                ]
            ),
        },
    )

    assert payload["language"] == "java"
    assert "exception_swallowed" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "exception_swallowed")
    assert observation["kind"] == "error_handling_weakened"
    assert observation["line_start"] == 54
    assert any("printStackTrace" in item or "catch" in item for item in observation["evidence"])


def test_code_observation_extractor_locates_java_swallowed_exception_in_later_hunk() -> None:
    extractor = CodeObservationExtractor()
    full_diff = "\n".join(
        [
            "diff --git a/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java b/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
            "@@ -20,7 +20,7 @@ public class MySqlDomainEventsConsumer {",
            " \tprivate final EventBus bus;",
            "-\tprivate final Integer CHUNKS = 200;",
            "+\tprivate final Integer chunksTmp = 200;",
            "@@ -56,7 +54,6 @@ public class MySqlDomainEventsConsumer {",
            " \t\t\t\t}",
            " \t\t\t} catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException |",
            " \t\t\t\t\t InstantiationException e) {",
            "-\t\t\t\te.printStackTrace();",
            " \t\t\t}",
        ]
    )

    payload = extractor.extract(
        file_path="src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        target_hunk={
            "start_line": 20,
            "changed_lines": [23],
            "excerpt": "\n".join(
                [
                    "@@ -20,7 +20,7 @@ public class MySqlDomainEventsConsumer {",
                    " \tprivate final EventBus bus;",
                    "-\tprivate final Integer CHUNKS = 200;",
                    "+\tprivate final Integer chunksTmp = 200;",
                ]
            ),
        },
        full_diff=full_diff,
    )

    observation = next(item for item in payload["observations"] if item["signal"] == "exception_swallowed")
    assert observation["line_start"] == 55


def test_code_observation_extractor_detects_go_loop_call_amplification() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="internal/order/service.go",
        target_hunk={
            "start_line": 48,
            "changed_lines": [48, 49],
            "excerpt": "\n".join(
                [
                    "@@ -48,0 +48,4 @@",
                    "+for _, order := range orders {",
                    "+    repository.Save(ctx, order)",
                    "+}",
                ]
            ),
        },
    )

    assert payload["language"] == "go"
    assert "loop_call_amplification" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "loop_call_amplification")
    assert observation["kind"] == "control_flow_with_external_call"


def test_code_observation_extractor_detects_python_mutable_default_arg() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="backend/app/services/cart.py",
        target_hunk={
            "start_line": 10,
            "changed_lines": [10],
            "excerpt": "\n".join(
                [
                    "@@ -10,0 +10,2 @@",
                    "+def append_item(item, bucket=[]):",
                    "+    bucket.append(item)",
                ]
            ),
        },
    )

    assert payload["language"] == "python"
    assert "python_mutable_default_arg" in payload["signals"]
    observation = next(item for item in payload["observations"] if item["signal"] == "python_mutable_default_arg")
    assert observation["kind"] == "mutable_default_argument"


def test_code_observation_extractor_detects_go_unchecked_error_and_goroutine_risk() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="internal/order/worker.go",
        target_hunk={
            "start_line": 22,
            "changed_lines": [22, 23],
            "excerpt": "\n".join(
                [
                    "@@ -22,0 +22,3 @@",
                    "+result, _ := client.Fetch(orderID)",
                    "+go publishOrder(result)",
                    "+return result",
                ]
            ),
        },
    )

    assert payload["language"] == "go"
    assert "go_unchecked_error_return" in payload["signals"]
    assert "go_goroutine_leak_risk" in payload["signals"]


def test_code_observation_extractor_detects_typescript_type_safety_escape() -> None:
    extractor = CodeObservationExtractor()

    payload = extractor.extract(
        file_path="frontend/src/user/profile.ts",
        target_hunk={
            "start_line": 18,
            "changed_lines": [18, 19],
            "excerpt": "\n".join(
                [
                    "@@ -18,0 +18,2 @@",
                    "+const payload: any = response.data;",
                    "+return payload.user!.name;",
                ]
            ),
        },
    )

    assert payload["language"] == "typescript"
    assert "typescript_any_type" in payload["signals"]
    assert "typescript_non_null_assertion" in payload["signals"]
