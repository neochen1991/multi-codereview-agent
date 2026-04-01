from pathlib import Path

from app.services.java_ddd_context_assembler import JavaDddContextAssembler
from app.services.repository_context_service import RepositoryContextService


def test_java_ddd_context_assembler_extracts_callees_and_transaction_chain(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    controller = repo_root / "src" / "main" / "java" / "com" / "acme" / "order" / "interfaces" / "OrderController.java"
    app_service = repo_root / "src" / "main" / "java" / "com" / "acme" / "order" / "app" / "OrderApplicationService.java"
    repository = repo_root / "src" / "main" / "java" / "com" / "acme" / "order" / "domain" / "OrderRepository.java"
    domain_service = repo_root / "src" / "main" / "java" / "com" / "acme" / "inventory" / "domain" / "InventoryDomainService.java"
    aggregate = repo_root / "src" / "main" / "java" / "com" / "acme" / "order" / "domain" / "OrderAggregate.java"
    for path in [controller, app_service, repository, domain_service, aggregate]:
        path.parent.mkdir(parents=True, exist_ok=True)

    controller.write_text(
        (
            "package com.acme.order.interfaces;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "import com.acme.order.app.OrderApplicationService;\n"
            "@RestController\n"
            "public class OrderController {\n"
            "  private final OrderApplicationService orderApplicationService;\n"
            "  public void close(Long id) { orderApplicationService.close(id); }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    app_service.write_text(
        (
            "package com.acme.order.app;\n"
            "import org.springframework.transaction.annotation.Transactional;\n"
            "import com.acme.order.domain.OrderRepository;\n"
            "import com.acme.inventory.domain.InventoryDomainService;\n"
            "public class OrderApplicationService {\n"
            "  private final OrderRepository orderRepository;\n"
            "  private final InventoryDomainService inventoryDomainService;\n"
            "  @Transactional\n"
            "  public void close(Long id) {\n"
            "    orderRepository.save(id);\n"
            "    inventoryDomainService.reserve(id);\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    repository.write_text(
        "package com.acme.order.domain;\npublic interface OrderRepository { void save(Long id); }\n",
        encoding="utf-8",
    )
    domain_service.write_text(
        "package com.acme.inventory.domain;\npublic class InventoryDomainService { public void reserve(Long id) {} }\n",
        encoding="utf-8",
    )
    aggregate.write_text(
        "package com.acme.order.domain;\npublic class OrderAggregate { public void close() {} }\n",
        encoding="utf-8",
    )

    service = RepositoryContextService(
        clone_url="https://github.com/example/acme.git",
        local_path=repo_root,
        default_branch="main",
    )
    assembler = JavaDddContextAssembler()

    context = assembler.build_context_pack(
        service,
        file_path="src/main/java/com/acme/order/app/OrderApplicationService.java",
        line_start=9,
        primary_context={"snippet": "orderRepository.save(id);\ninventoryDomainService.reserve(id);\n"},
        related_files=[
            "src/main/java/com/acme/order/domain/OrderRepository.java",
            "src/main/java/com/acme/inventory/domain/InventoryDomainService.java",
        ],
        symbol_contexts=[{"symbol": "close"}],
        excerpt="orderRepository.save(id);\ninventoryDomainService.reserve(id);\n",
    )

    assert context["java_review_mode"] == "ddd_enhanced"
    assert "application_service_layer" in context["java_context_signals"]
    assert "ddd_package_layout" in context["java_context_signals"]
    assert "domain_service_dependency" in context["java_context_signals"]
    callee_symbols = {str(item.get("symbol") or "") for item in context["callee_contexts"]}
    assert "OrderRepository" in callee_symbols
    assert "InventoryDomainService" in callee_symbols

    caller_paths = {str(item.get("path") or "") for item in context["caller_contexts"]}
    assert "src/main/java/com/acme/order/interfaces/OrderController.java" in caller_paths

    transaction_context = context["transaction_context"]
    assert transaction_context["transactional_method"] == "close"
    assert any("controller:" in item for item in transaction_context["call_chain"])
    assert any("repository:" in item for item in transaction_context["call_chain"])


def test_java_ddd_context_assembler_marks_regular_spring_java_as_general(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    controller = repo_root / "src" / "main" / "java" / "com" / "acme" / "user" / "web" / "UserController.java"
    service_file = repo_root / "src" / "main" / "java" / "com" / "acme" / "user" / "service" / "UserService.java"
    repository = repo_root / "src" / "main" / "java" / "com" / "acme" / "user" / "repository" / "UserRepository.java"
    mapper = repo_root / "src" / "main" / "resources" / "mapper" / "UserMapper.xml"
    for path in [controller, service_file, repository, mapper]:
        path.parent.mkdir(parents=True, exist_ok=True)

    controller.write_text(
        (
            "package com.acme.user.web;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "import com.acme.user.service.UserService;\n"
            "@RestController\n"
            "public class UserController {\n"
            "  private final UserService userService;\n"
            "  public void create(String name) { userService.create(name); }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    service_file.write_text(
        (
            "package com.acme.user.service;\n"
            "import org.springframework.transaction.annotation.Transactional;\n"
            "import com.acme.user.repository.UserRepository;\n"
            "public class UserService {\n"
            "  private final UserRepository userRepository;\n"
            "  @Transactional\n"
            "  public void create(String name) {\n"
            "    userRepository.insert(name);\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    repository.write_text(
        "package com.acme.user.repository;\npublic interface UserRepository { void insert(String name); }\n",
        encoding="utf-8",
    )
    mapper.write_text(
        "<mapper namespace=\"UserMapper\"><insert id=\"insert\">insert into user(name) values(#{name})</insert></mapper>\n",
        encoding="utf-8",
    )

    service = RepositoryContextService(
        clone_url="https://github.com/example/acme.git",
        local_path=repo_root,
        default_branch="main",
    )
    assembler = JavaDddContextAssembler()

    context = assembler.build_context_pack(
        service,
        file_path="src/main/java/com/acme/user/service/UserService.java",
        line_start=7,
        primary_context={"snippet": "userRepository.insert(name);\n"},
        related_files=[
            "src/main/java/com/acme/user/repository/UserRepository.java",
            "src/main/resources/mapper/UserMapper.xml",
        ],
        symbol_contexts=[{"symbol": "create"}],
        excerpt="userRepository.insert(name);\n",
    )

    assert context["java_review_mode"] == "general"
    assert "controller_entry" in context["java_context_signals"]
    assert "transaction_boundary" in context["java_context_signals"]
    assert "repository_dependency" in context["java_context_signals"]
    assert "sql_or_mapper_context" in context["java_context_signals"]
