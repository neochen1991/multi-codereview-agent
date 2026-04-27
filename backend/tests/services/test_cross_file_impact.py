from app.services.cross_file_impact import build_cross_file_impact_hints


def test_cross_file_impact_flags_signature_change_with_unchanged_callers():
    hints = build_cross_file_impact_hints(
        file_path="src/main/java/com/example/OwnerRepository.java",
        changed_files=["src/main/java/com/example/OwnerRepository.java"],
        target_hunk_excerpt=(
            "  10 | - public Page<Owner> findByLastNameStartingWith(String lastName, Pageable pageable);\n"
            "  10 | + public List<Owner> findByLastNameContaining(String lastName);\n"
        ),
        repository_context={
            "symbol_contexts": [
                {
                    "symbol": "findByLastNameContaining",
                    "definitions": [
                        {
                            "path": "src/main/java/com/example/OwnerRepository.java",
                            "line_number": 10,
                            "snippet": "public List<Owner> findByLastNameContaining(String lastName);",
                        }
                    ],
                    "references": [
                        {
                            "path": "src/main/java/com/example/OwnerController.java",
                            "line_number": 32,
                            "snippet": "owners.findByLastNameStartingWith(lastName, pageRequest);",
                        }
                    ],
                }
            ],
            "caller_contexts": [
                {
                    "path": "src/main/java/com/example/OwnerController.java",
                    "line_start": 28,
                    "snippet": "owners.findByLastNameStartingWith(lastName, pageRequest);",
                }
            ],
        },
    )

    assert any("签名级变更" in item for item in hints)
    assert any("返回类型由 Page<Owner> 变为 List<Owner>" in item for item in hints)
    assert any("分页返回对象变成集合返回" in item for item in hints)
    assert any("调用方未随这次签名变更一起修改" in item for item in hints)
    assert any("调用点仍保留旧调用形态" in item for item in hints)


def test_cross_file_impact_does_not_flag_signature_change_for_non_signature_hunk():
    hints = build_cross_file_impact_hints(
        file_path="src/main/java/com/example/OwnerService.java",
        changed_files=["src/main/java/com/example/OwnerService.java"],
        target_hunk_excerpt=(
            "  18 | -     owner.setActive(false);\n"
            "  18 | +     owner.setActive(true);\n"
        ),
        repository_context={
            "caller_contexts": [],
            "symbol_contexts": [],
        },
    )

    assert all("签名级变更" not in item for item in hints)


def test_cross_file_impact_counts_generic_parameters_and_nested_call_arguments():
    hints = build_cross_file_impact_hints(
        file_path="src/main/java/com/example/SearchService.java",
        changed_files=["src/main/java/com/example/SearchService.java"],
        target_hunk_excerpt=(
            "  18 | - public SearchResult search(Map<String, Object> filters, String keyword);\n"
            "  18 | + public SearchResult search(Map<String, Object> filters, String keyword, Pageable pageable);\n"
        ),
        repository_context={
            "symbol_contexts": [
                {
                    "symbol": "search",
                    "references": [
                        {
                            "path": "src/main/java/com/example/SearchController.java",
                            "line_number": 41,
                            "snippet": "searchService.search(filters, normalize(keyword, locale));",
                        }
                    ],
                }
            ],
        },
    )

    assert any("入参数量由 2 个变为 3 个" in item for item in hints)
    assert any("调用点仍保留旧调用形态" in item for item in hints)


def test_cross_file_impact_flags_exception_contract_change():
    hints = build_cross_file_impact_hints(
        file_path="src/main/java/com/example/PaymentClient.java",
        changed_files=["src/main/java/com/example/PaymentClient.java"],
        target_hunk_excerpt=(
            "  27 | - public Receipt charge(Order order) throws PaymentTimeoutException;\n"
            "  27 | + public Receipt charge(Order order);\n"
        ),
        repository_context={
            "caller_contexts": [
                {
                    "path": "src/main/java/com/example/OrderService.java",
                    "line_start": 88,
                    "snippet": "try { client.charge(order); } catch (PaymentTimeoutException ex) { retry(order); }",
                }
            ],
        },
    )

    assert any("抛出异常契约发生变化" in item for item in hints)
    assert any("显式异常声明被移除" in item for item in hints)
