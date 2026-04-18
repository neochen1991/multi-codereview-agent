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

    assert payload == {
        "language": "text",
        "signals": [],
        "summary": "",
        "matched_terms": [],
        "signal_terms": {},
        "observations": [],
    }
