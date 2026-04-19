from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import PostgresDataSourceSettings, RuntimeSettings
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


def test_skill_gateway_repo_context_search_uses_workspace_fallback_for_manual_review(tmp_path: Path, monkeypatch):
    storage_root = tmp_path / "storage"
    workspace = tmp_path / "workspace"
    primary = workspace / "src" / "service.ts"
    git_dir = workspace / ".git"
    git_dir.mkdir(parents=True)
    primary.parent.mkdir(parents=True)
    primary.write_text("export const foo = () => transform()\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

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
        changed_files=["src/service.ts"],
        unified_diff=(
            "diff --git a/src/service.ts b/src/service.ts\n"
            "--- a/src/service.ts\n"
            "+++ b/src/service.ts\n"
            "@@ -0,0 +1 @@\n"
            "+export const foo = () => transform()\n"
        ),
        metadata={"trigger_source": "manual"},
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="",
        code_repo_local_path="",
        code_repo_default_branch="main",
        runtime_tool_allowlist=["repo_context_search"],
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="src/service.ts",
        line_start=1,
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    assert repo_result["primary_context"]["path"] == "src/service.ts"
    assert "foo" in str(repo_result["primary_context"]["snippet"])


def test_skill_gateway_repo_context_search_only_returns_source_file_names_for_hits(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    consumer = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderConsumer.java"
    compiled = repo_root / "target" / "classes" / "com" / "example" / "OrderConsumer.class"
    test_file = repo_root / "src" / "test" / "java" / "com" / "example" / "OrderConsumerTest.java"
    primary.parent.mkdir(parents=True, exist_ok=True)
    consumer.parent.mkdir(parents=True, exist_ok=True)
    compiled.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    primary.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class OrderService {",
                "    public void processOrder() {}",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    consumer.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class OrderConsumer {",
                "    private final OrderService orderService = new OrderService();",
                "    public void consume() {",
                "        orderService.processOrder();",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    compiled.write_text("processOrder\n", encoding="utf-8")
    test_file.write_text("new OrderService().processOrder();\n", encoding="utf-8")

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能",
        role="performance",
        enabled=True,
        focus_areas=["性能热点"],
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
            "@@ -2,1 +2,1 @@\n"
            "-    public void processOrder() {}\n"
            "+    public void processOrder() { audit(); }\n"
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
        line_start=2,
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    assert "src/main/java/com/example/OrderService.java" in repo_result["definition_hits"]
    assert "src/main/java/com/example/OrderConsumer.java" in repo_result["reference_hits"]
    assert len(repo_result["definition_hits"]) == len(set(repo_result["definition_hits"]))
    assert len(repo_result["reference_hits"]) == len(set(repo_result["reference_hits"]))
    assert all(item.endswith((".java", ".kt", ".groovy", ".scala", ".cs", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rb", ".php")) for item in repo_result["definition_hits"] + repo_result["reference_hits"])


def test_skill_gateway_repo_context_search_filters_test_named_java_classes(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    prod_ref = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderConsumer.java"
    test_named_ref = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderConsumerTest.java"
    primary.parent.mkdir(parents=True, exist_ok=True)
    prod_ref.parent.mkdir(parents=True, exist_ok=True)
    test_named_ref.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(
        "public class OrderService { public void processOrder() {} }\n",
        encoding="utf-8",
    )
    prod_ref.write_text(
        "public class OrderConsumer { void consume(OrderService s) { s.processOrder(); } }\n",
        encoding="utf-8",
    )
    test_named_ref.write_text(
        "public class OrderConsumerTest { void verify(OrderService s) { s.processOrder(); } }\n",
        encoding="utf-8",
    )

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能",
        role="performance",
        enabled=True,
        focus_areas=["性能热点"],
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
            "@@ -1,1 +1,1 @@\n"
            "-public class OrderService { public void processOrder() {} }\n"
            "+public class OrderService { public void processOrder() { audit(); } }\n"
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
        line_start=1,
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    assert "src/main/java/com/example/OrderConsumer.java" in repo_result["reference_hits"]
    assert all("Test.java" not in item for item in repo_result["definition_hits"])
    assert all("Test.java" not in item for item in repo_result["reference_hits"])


def test_skill_gateway_repo_context_search_collects_related_source_snippets_for_prompt(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    consumer = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderConsumer.java"
    primary.parent.mkdir(parents=True, exist_ok=True)
    consumer.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class OrderService {",
                "    public void processOrder(String id) {",
                "        audit(id);",
                "    }",
                "    private void audit(String id) {}",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    consumer.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class OrderConsumer {",
                "    private final OrderService orderService = new OrderService();",
                "    public void consume(String id) {",
                "        orderService.processOrder(id);",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能",
        role="performance",
        enabled=True,
        focus_areas=["性能热点"],
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
            "@@ -3,1 +3,1 @@\n"
            "-        audit(id);\n"
            "+        audit(id.trim());\n"
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
        line_start=3,
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    assert repo_result["related_source_snippets"]
    assert any(
        item["path"] == "src/main/java/com/example/OrderConsumer.java" and "processOrder" in item["snippet"]
        for item in repo_result["related_source_snippets"]
    )


def test_skill_gateway_repo_context_search_prefers_symbol_hit_lines_for_related_contexts(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "main" / "java" / "com" / "example" / "OwnerController.java"
    related = repo_root / "src" / "main" / "java" / "com" / "example" / "OwnerRepository.java"
    primary.parent.mkdir(parents=True, exist_ok=True)
    related.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class OwnerController {",
                "    private final OwnerRepository ownerRepository;",
                "    public OwnerController(OwnerRepository ownerRepository) {",
                "        this.ownerRepository = ownerRepository;",
                "    }",
                "    public void updateOwner(String lastName) {",
                "        ownerRepository.findByLastNameContaining(lastName);",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    related.write_text(
        "\n".join(
            [
                "/*",
                " * Copyright header line 1",
                " * Copyright header line 2",
                " * Copyright header line 3",
                " * Copyright header line 4",
                " */",
                "package com.example;",
                "public interface OwnerRepository {",
                "    java.util.List<String> findByLastNameContaining(String lastName);",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Architecture",
        name_zh="架构",
        role="architecture",
        enabled=True,
        focus_areas=["分层边界"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/owner",
        target_ref="main",
        changed_files=["src/main/java/com/example/OwnerController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OwnerController.java b/src/main/java/com/example/OwnerController.java\n"
            "--- a/src/main/java/com/example/OwnerController.java\n"
            "+++ b/src/main/java/com/example/OwnerController.java\n"
            "@@ -7,1 +7,1 @@\n"
            "-        ownerRepository.findByLastName(lastName);\n"
            "+        ownerRepository.findByLastNameContaining(lastName);\n"
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
        file_path="src/main/java/com/example/OwnerController.java",
        line_start=8,
        related_files=["src/main/java/com/example/OwnerRepository.java"],
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    related_context = next(
        item
        for item in repo_result["related_contexts"]
        if item["path"] == "src/main/java/com/example/OwnerRepository.java"
    )
    assert related_context["line_start"] >= 7
    assert "findByLastNameContaining" in related_context["snippet"]
    assert "Copyright header line 1" not in related_context["snippet"]


def test_skill_gateway_repo_context_search_prefers_meaningful_related_source_snippets(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "main" / "java" / "com" / "example" / "OwnerController.java"
    related = repo_root / "src" / "main" / "java" / "com" / "example" / "OwnerRepository.java"
    primary.parent.mkdir(parents=True, exist_ok=True)
    related.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class OwnerController {",
                "    private final OwnerRepository ownerRepository;",
                "    public OwnerController(OwnerRepository ownerRepository) {",
                "        this.ownerRepository = ownerRepository;",
                "    }",
                "    public void updateOwner(String lastName) {",
                "        ownerRepository.findByLastNameContaining(lastName);",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    related.write_text(
        "\n".join(
            [
                "/**",
                " * Copyright header line 1",
                " * Copyright header line 2",
                " * @author Demo",
                " */",
                "package com.example;",
                "public interface OwnerRepository {",
                "    java.util.List<String> findByLastNameContaining(String lastName);",
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
        source_ref="feature/owner",
        target_ref="main",
        changed_files=["src/main/java/com/example/OwnerController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OwnerController.java b/src/main/java/com/example/OwnerController.java\n"
            "--- a/src/main/java/com/example/OwnerController.java\n"
            "+++ b/src/main/java/com/example/OwnerController.java\n"
            "@@ -7,1 +7,1 @@\n"
            "-        ownerRepository.findByLastName(lastName);\n"
            "+        ownerRepository.findByLastNameContaining(lastName);\n"
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
        file_path="src/main/java/com/example/OwnerController.java",
        line_start=8,
        related_files=["src/main/java/com/example/OwnerRepository.java"],
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    related_snippet = next(
        item
        for item in repo_result["related_source_snippets"]
        if item["path"] == "src/main/java/com/example/OwnerRepository.java"
    )
    assert related_snippet["line_start"] >= 7
    assert "findByLastNameContaining" in related_snippet["snippet"]
    assert "Copyright header line 1" not in related_snippet["snippet"]


def test_skill_gateway_repo_context_search_prefers_diff_hunk_symbol_for_related_source_snippets(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "main" / "java" / "com" / "example" / "OwnerController.java"
    related = repo_root / "src" / "main" / "java" / "com" / "example" / "PetController.java"
    primary.parent.mkdir(parents=True, exist_ok=True)
    related.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class OwnerController {",
                "    public Owner findOwner(int ownerId) { return null; }",
                "    public void setAllowedFields(Object binder) {}",
                "    public String processCreationForm(Object owner, Object result) {",
                "        return \"ok\";",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    related.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class PetController {",
                "    public Owner findOwner(int ownerId) { return null; }",
                "    public void setAllowedFields(Object binder) {}",
                "    public String processCreationForm(Object owner, Object result) {",
                "        return \"ok\";",
                "    }",
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
        source_ref="feature/owner",
        target_ref="main",
        changed_files=["src/main/java/com/example/OwnerController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OwnerController.java b/src/main/java/com/example/OwnerController.java\n"
            "--- a/src/main/java/com/example/OwnerController.java\n"
            "+++ b/src/main/java/com/example/OwnerController.java\n"
            "@@ -5,1 +5,1 @@\n"
            "-    public String processCreationForm(@Valid Object owner, Object result) {\n"
            "+    public String processCreationForm(Object owner, Object result) {\n"
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
        file_path="src/main/java/com/example/OwnerController.java",
        line_start=5,
        related_files=["src/main/java/com/example/PetController.java"],
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    related_snippet = next(
        item
        for item in repo_result["related_source_snippets"]
        if item["path"] == "src/main/java/com/example/PetController.java"
    )
    assert "processCreationForm" in related_snippet["snippet"]
    related_context = next(
        item
        for item in repo_result["related_contexts"]
        if item["path"] == "src/main/java/com/example/PetController.java"
    )
    assert "processCreationForm" in related_context["snippet"]


def test_skill_gateway_repo_context_search_derives_related_contexts_without_explicit_related_files(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "main" / "java" / "com" / "example" / "OwnerController.java"
    related = repo_root / "src" / "main" / "java" / "com" / "example" / "PetController.java"
    primary.parent.mkdir(parents=True, exist_ok=True)
    related.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class OwnerController {",
                "    public void setAllowedFields(Object binder) {}",
                "    public Owner findOwner(int ownerId) { return null; }",
                "    public String processCreationForm(Object owner, Object result) {",
                "        return \"ok\";",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    related.write_text(
        "\n".join(
            [
                "package com.example;",
                "public class PetController {",
                "    public Owner findOwner(int ownerId) { return null; }",
                "    public void setAllowedFields(Object binder) {}",
                "    public String processCreationForm(Object owner, Object result) {",
                "        return \"ok\";",
                "    }",
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
        source_ref="feature/owner",
        target_ref="main",
        changed_files=["src/main/java/com/example/OwnerController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OwnerController.java b/src/main/java/com/example/OwnerController.java\n"
            "--- a/src/main/java/com/example/OwnerController.java\n"
            "+++ b/src/main/java/com/example/OwnerController.java\n"
            "@@ -5,1 +5,1 @@\n"
            "-    public String processCreationForm(@Valid Object owner, Object result) {\n"
            "+    public String processCreationForm(Object owner, Object result) {\n"
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
        file_path="src/main/java/com/example/OwnerController.java",
        line_start=5,
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    related_context = next(
        item
        for item in repo_result["related_contexts"]
        if item["path"] == "src/main/java/com/example/PetController.java"
    )
    assert "processCreationForm" in related_context["snippet"]
    related_snippet = next(
        item
        for item in repo_result["related_source_snippets"]
        if item["path"] == "src/main/java/com/example/PetController.java"
    )
    assert "processCreationForm" in related_snippet["snippet"]


def test_skill_gateway_pg_schema_context_for_database_expert(tmp_path: Path, monkeypatch) -> None:
    storage_root = tmp_path / "storage"
    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="database_analysis",
        name="Database",
        name_zh="数据库",
        role="database",
        enabled=True,
        focus_areas=["schema 变更", "索引与统计信息"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        repo_url="https://github.com/example/repo.git",
        source_ref="feature/db",
        target_ref="main",
        changed_files=["db/migration/V1__orders.sql"],
        unified_diff='ALTER TABLE "orders" ADD COLUMN "status" varchar(32);',
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        runtime_tool_allowlist=["pg_schema_context"],
        database_sources=[
            PostgresDataSourceSettings(
                repo_url="https://github.com/example/repo.git",
                provider="postgres",
                host="127.0.0.1",
                port=5432,
                database="review_db",
                user="review_user",
                password_env="PG_REVIEW_PASSWORD",
                schema_allowlist=["public"],
            )
        ],
    )

    class _FakeContext:
        matched = True
        summary = "已从 PostgreSQL 数据源拉取 1 张表的结构与统计元信息。"

        def to_payload(self):
            return {
                "summary": self.summary,
                "matched": True,
                "degraded_reason": "",
                "data_source_summary": {
                    "repo_url": "https://github.com/example/repo.git",
                    "provider": "postgres",
                    "host": "127.0.0.1",
                    "port": 5432,
                    "database": "review_db",
                    "user": "review_user",
                    "schema_allowlist": ["public"],
                    "ssl_mode": "prefer",
                },
                "matched_tables": ["orders"],
                "meta_queries": ["table_columns", "constraints", "indexes", "table_stats"],
                "table_columns": [
                    {
                        "table_schema": "public",
                        "table_name": "orders",
                        "column_name": "status",
                        "data_type": "character varying",
                        "is_nullable": "YES",
                        "column_default": "",
                    }
                ],
                "constraints": [{"table_name": "orders", "constraint_type": "PRIMARY KEY", "columns": "id"}],
                "indexes": [{"table_name": "orders", "indexname": "idx_orders_status"}],
                "table_stats": [{"table_name": "orders", "estimated_rows": 1024}],
            }

    monkeypatch.setattr(
        gateway._postgres_metadata,
        "collect_context",
        lambda *args, **kwargs: _FakeContext(),
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="db/migration/V1__orders.sql",
        line_start=1,
    )

    tool_names = {item["tool_name"] for item in results}
    assert "pg_schema_context" in tool_names
    pg_result = next(item for item in results if item["tool_name"] == "pg_schema_context")
    assert pg_result["success"] is True
    assert pg_result["matched_tables"] == ["orders"]
    assert pg_result["data_source_summary"]["database"] == "review_db"


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


def test_skill_gateway_auto_adds_java_ddd_runtime_tools(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    app_service = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderApplicationService.java"
    aggregate = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderAggregate.java"
    repository = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderRepository.java"
    app_service.parent.mkdir(parents=True, exist_ok=True)
    app_service.write_text(
        "\n".join(
            [
                "package com.example;",
                "import org.springframework.transaction.annotation.Transactional;",
                "public class OrderApplicationService {",
                "    private final OrderRepository orderRepository;",
                "    @Transactional",
                "    public void close(OrderAggregate order) {",
                "        order.setStatus(\"CLOSED\");",
                "        orderRepository.save(order);",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    aggregate.write_text("public class OrderAggregate { public void setStatus(String status) {} }\n", encoding="utf-8")
    repository.write_text("public interface OrderRepository { void save(OrderAggregate order); }\n", encoding="utf-8")

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD Architecture",
        name_zh="DDD架构",
        role="ddd architecture",
        enabled=True,
        focus_areas=["DDD", "事务边界"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/order",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderApplicationService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderApplicationService.java "
            "b/src/main/java/com/example/OrderApplicationService.java\n"
            "--- a/src/main/java/com/example/OrderApplicationService.java\n"
            "+++ b/src/main/java/com/example/OrderApplicationService.java\n"
            "@@ -6,1 +6,2 @@\n"
            "-        orderRepository.save(order);\n"
            "+        order.setStatus(\"CLOSED\");\n"
            "+        orderRepository.save(order);\n"
        ),
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        code_repo_local_path=str(repo_root),
        code_repo_default_branch="main",
        runtime_tool_allowlist=[
            "repo_context_search",
            "application_service_boundary_inspector",
            "aggregate_invariant_inspector",
        ],
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="src/main/java/com/example/OrderApplicationService.java",
        line_start=6,
    )

    tool_names = {item["tool_name"] for item in results}
    assert "application_service_boundary_inspector" in tool_names
    assert "aggregate_invariant_inspector" in tool_names
    boundary = next(item for item in results if item["tool_name"] == "application_service_boundary_inspector")
    aggregate_result = next(item for item in results if item["tool_name"] == "aggregate_invariant_inspector")
    assert "应用服务直接进行持久化写入" in boundary["summary"]
    assert "检测到直接状态修改" in aggregate_result["summary"]


def test_skill_gateway_auto_adds_java_general_runtime_tools(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    controller = repo_root / "src" / "main" / "java" / "com" / "example" / "UserController.java"
    service_file = repo_root / "src" / "main" / "java" / "com" / "example" / "UserService.java"
    repository = repo_root / "src" / "main" / "java" / "com" / "example" / "UserRepository.java"
    mapper = repo_root / "src" / "main" / "resources" / "mapper" / "UserMapper.xml"
    for path in [controller, service_file, repository, mapper]:
        path.parent.mkdir(parents=True, exist_ok=True)

    controller.write_text(
        (
            "import org.springframework.web.bind.annotation.RestController;\n"
            "public class UserController {\n"
            "  private final UserService userService;\n"
            "  public void create(UserCreateRequest request) { userService.create(request); }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    service_file.write_text(
        (
            "import org.springframework.transaction.annotation.Transactional;\n"
            "public class UserService {\n"
            "  private final UserRepository userRepository;\n"
            "  @Transactional\n"
            "  public void create(UserCreateRequest request) {\n"
            "    userRepository.findByStatus(request.status());\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    repository.write_text(
        "public interface UserRepository { java.util.List<UserRecord> findByStatus(String status); }\n",
        encoding="utf-8",
    )
    mapper.write_text(
        (
            "<mapper namespace=\"UserMapper\">\n"
            "  <select id=\"findByStatus\">select * from user where status = #{status}</select>\n"
            "</mapper>\n"
        ),
        encoding="utf-8",
    )

    gateway = ReviewToolGateway(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/user-query",
        target_ref="main",
        changed_files=[
            "src/main/java/com/example/UserService.java",
            "src/main/java/com/example/UserController.java",
            "src/main/resources/mapper/UserMapper.xml",
        ],
        unified_diff=(
            "diff --git a/src/main/java/com/example/UserService.java b/src/main/java/com/example/UserService.java\n"
            "--- a/src/main/java/com/example/UserService.java\n"
            "+++ b/src/main/java/com/example/UserService.java\n"
            "@@ -4,1 +4,3 @@\n"
            "  public void create(UserCreateRequest request) {\n"
            "+   userRepository.findByStatus(request.status());\n"
            "  }\n"
        ),
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        code_repo_local_path=str(repo_root),
        code_repo_default_branch="main",
        runtime_tool_allowlist=[
            "repo_context_search",
            "controller_entry_guard_inspector",
            "repository_query_risk_inspector",
            "transaction_boundary_inspector",
        ],
    )

    security_expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全",
        role="security",
        enabled=True,
        focus_areas=["接口校验", "权限边界"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    db_expert = ExpertProfile(
        expert_id="database_analysis",
        name="Database",
        name_zh="数据库",
        role="database",
        enabled=True,
        focus_areas=["查询风险"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )

    security_results = gateway.invoke_for_expert(
        security_expert,
        subject,
        runtime,
        file_path="src/main/java/com/example/UserService.java",
        line_start=5,
    )
    db_results = gateway.invoke_for_expert(
        db_expert,
        subject,
        runtime,
        file_path="src/main/java/com/example/UserService.java",
        line_start=5,
    )

    security_tool_names = {item["tool_name"] for item in security_results}
    db_tool_names = {item["tool_name"] for item in db_results}
    assert "controller_entry_guard_inspector" in security_tool_names
    assert "repository_query_risk_inspector" in db_tool_names

    entry_guard = next(item for item in security_results if item["tool_name"] == "controller_entry_guard_inspector")
    query_risk = next(item for item in db_results if item["tool_name"] == "repository_query_risk_inspector")
    assert "接口入口未见明显参数校验信号" in entry_guard["summary"]
    assert "检测到按状态或条件批量查询信号" in query_risk["summary"]
    assert "当前查询片段未见明显分页/limit 保护" in query_risk["summary"]


def test_aggregate_invariant_inspector_detects_factory_bypass_and_event_publish(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    service_file = repo_root / "src" / "main" / "java" / "com" / "example" / "CourseCreator.java"
    service_file.parent.mkdir(parents=True, exist_ok=True)
    service_file.write_text(
        (
            "package com.example;\n"
            "\n"
            "public final class CourseCreator {\n"
            "  public void create(CourseId id, CourseName name, CourseDuration duration) {\n"
            "    Course course = new Course(id, name, duration);\n"
            "    repository.save(course);\n"
            "    eventBus.publish(course.pullDomainEvents());\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    gateway = ReviewToolGateway(tmp_path / "storage")

    result = gateway._aggregate_invariant_inspector(
        {
            "repo_local_path": str(repo_root),
            "file_path": "src/main/java/com/example/CourseCreator.java",
            "line_start": 5,
            "subject": {
                "subject_type": "mr",
                "repo_id": "repo",
                "project_id": "proj",
                "source_ref": "feature/course",
                "target_ref": "main",
                "changed_files": ["src/main/java/com/example/CourseCreator.java"],
                "unified_diff": (
                    "diff --git a/src/main/java/com/example/CourseCreator.java "
                    "b/src/main/java/com/example/CourseCreator.java\n"
                    "--- a/src/main/java/com/example/CourseCreator.java\n"
                    "+++ b/src/main/java/com/example/CourseCreator.java\n"
                    "@@ -4,7 +4,7 @@ public final class CourseCreator {\n"
                    "-        Course course = Course.create(id, name, duration);\n"
                    "+        Course course = new Course(id, name, duration);\n"
                ),
            },
            "target_hunk": {
                "excerpt": (
                    "@@ -4,7 +4,7 @@ public final class CourseCreator {\n"
                    "   - |         Course course = Course.create(id, name, duration);\n"
                    "  5 | +        Course course = new Course(id, name, duration);\n"
                    "  6 |          repository.save(course);\n"
                    "  7 |          eventBus.publish(course.pullDomainEvents());\n"
                )
            },
        }
    )

    assert result["success"] is True
    assert "aggregate_factory_bypass_detected" in result["signals"]
    assert "domain_event_publish_after_direct_construction" in result["signals"]
    assert "Course" in result["aggregate_symbols"]
    assert "聚合工厂" in result["summary"]
