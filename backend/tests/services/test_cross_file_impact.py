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
