from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.tool_gateway import ReviewToolGateway


def test_skill_gateway_adds_repo_context_search_for_all_experts(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    target = repo_root / "src" / "service.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const foo = () => repo.search()\n", encoding="utf-8")

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
        runtime_tool_bindings=["knowledge_search"],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/service.ts"],
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        code_repo_local_path=str(repo_root),
        code_repo_default_branch="main",
        runtime_tool_allowlist=["knowledge_search", "repo_context_search"],
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="src/service.ts",
        line_start=1,
    )

    tool_names = {item["tool_name"] for item in results}
    assert "repo_context_search" in tool_names


def test_skill_gateway_repo_context_search_returns_related_contexts(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "service.ts"
    related = repo_root / "src" / "transform.ts"
    test_file = repo_root / "src" / "__tests__" / "transform.test.ts"
    primary.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    primary.write_text("export const foo = () => transform()\n", encoding="utf-8")
    related.write_text("export const transform = () => 'ok'\n", encoding="utf-8")
    test_file.write_text("describe('transform', () => transform())\n", encoding="utf-8")

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/service.ts", "src/transform.ts"],
        unified_diff=(
            "diff --git a/src/service.ts b/src/service.ts\n"
            "--- a/src/service.ts\n"
            "+++ b/src/service.ts\n"
            "@@ -0,0 +1 @@\n"
            "+export const foo = () => transform()\n"
        ),
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        code_repo_local_path=str(repo_root),
        code_repo_default_branch="main",
        runtime_tool_allowlist=["repo_context_search"],
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="src/service.ts",
        line_start=1,
        related_files=["src/transform.ts"],
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    assert "src/service.ts" in repo_result["context_files"]
    assert "src/transform.ts" in repo_result["context_files"]
    assert repo_result["related_contexts"]
    assert "symbol_contexts" in repo_result
    assert repo_result["search_keywords"] == ["foo"]
    assert repo_result["search_keyword_sources"] == [
        {"keyword": "foo", "source": "diff_hunk", "source_label": "diff hunk"}
    ]
    assert repo_result["search_commands"]
    assert all("__tests__" not in item for item in repo_result["definition_hits"])
    assert all("__tests__" not in item for item in repo_result["reference_hits"])
    assert repo_result["symbol_match_strategy"] == "文本检索命中 + 轻量定义特征判断"
    assert "不是 AST 级静态分析" in repo_result["symbol_match_explanation"]


def test_skill_gateway_repo_context_search_derives_java_method_from_source_context(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    service_file = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    service_file.parent.mkdir(parents=True)
    service_file.write_text(
        "\n".join(
            [
                "package com.example;",
                "",
                "public class OrderService {",
                "    public void processOrder(String orderId) {",
                "        validate(orderId);",
                "        persist(orderId);",
                "    }",
                "",
                "    private void validate(String orderId) {}",
                "    private void persist(String orderId) {}",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/order",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n"
            "--- a/src/main/java/com/example/OrderService.java\n"
            "+++ b/src/main/java/com/example/OrderService.java\n"
            "@@ -5,1 +5,1 @@\n"
            "-        persist(orderId);\n"
            "+        persist(orderId.trim());\n"
        ),
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        code_repo_local_path=str(repo_root),
        code_repo_default_branch="main",
        runtime_tool_allowlist=["repo_context_search"],
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="src/main/java/com/example/OrderService.java",
        line_start=5,
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    assert repo_result["search_keywords"]
    assert "processOrder" in repo_result["search_keywords"]
    assert "OrderService" in repo_result["search_keywords"]
    assert {"keyword": "processOrder", "source": "source_context", "source_label": "源码上下文"} in repo_result["search_keyword_sources"]
    assert {"keyword": "OrderService", "source": "source_context", "source_label": "源码上下文"} in repo_result["search_keyword_sources"]
    assert "已按" in repo_result["summary"]


def test_skill_gateway_repo_context_search_falls_back_to_java_class_name_from_file_name(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    service_file = repo_root / "src" / "main" / "java" / "com" / "example" / "InventoryDomainService.java"
    service_file.parent.mkdir(parents=True)
    service_file.write_text(
        "\n".join(
            [
                "package com.example;",
                "",
                "public class InventoryDomainService {",
                "    // body omitted",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="architecture",
        name="Architecture",
        name_zh="架构",
        role="architecture",
        enabled=True,
        focus_areas=["架构边界"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/inventory",
        target_ref="main",
        changed_files=["src/main/java/com/example/InventoryDomainService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/InventoryDomainService.java b/src/main/java/com/example/InventoryDomainService.java\n"
            "--- a/src/main/java/com/example/InventoryDomainService.java\n"
            "+++ b/src/main/java/com/example/InventoryDomainService.java\n"
            "@@ -40,2 +40,2 @@\n"
            "-        // old logic\n"
            "+        // optimized logic\n"
        ),
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        code_repo_local_path=str(repo_root),
        code_repo_default_branch="main",
        runtime_tool_allowlist=["repo_context_search"],
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="src/main/java/com/example/InventoryDomainService.java",
        line_start=40,
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    assert repo_result["search_keywords"] == ["InventoryDomainService"]
    assert repo_result["search_keyword_sources"] == [
        {"keyword": "InventoryDomainService", "source": "file_name", "source_label": "文件名类名兜底"}
    ]
    assert "已按 1 个方法/类关键词检索目标分支代码仓" in repo_result["summary"]
