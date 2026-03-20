from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.repositories.sqlite_knowledge_rule_repository import SqliteKnowledgeRuleRepository
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_rule_index_service import KnowledgeRuleIndexService

REAL_PERF_RULES_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "expert-specs-export"
    / "performance_reliability"
    / "performance-reliability-real-rules.md"
)


def test_knowledge_rule_index_service_parses_rule_cards() -> None:
    service = KnowledgeRuleIndexService()
    document = KnowledgeDocument(
        title="性能规则",
        expert_id="performance_reliability",
        doc_type="review_rule",
        source_filename="perf-rules.md",
        content=(
            "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
            "### 一级场景\n"
            "并发与线程池\n\n"
            "### 二级场景\n"
            "线程池容量配置\n\n"
            "### 三级场景\n"
            "线程池扩容缺少容量评估\n\n"
            "### 描述\n"
            "检查线程池扩容是否同步评估下游容量。\n\n"
            "### 问题代码示例\n"
            "```java\nexecutor.setMaxPoolSize(32);\n```\n\n"
            "### 问题代码行\n"
            "executor.setMaxPoolSize(512);\n\n"
            "### 误报代码\n"
            "```java\nexecutor.setMaxPoolSize(32);\n```\n\n"
            "### 语言\n"
            "java\n\n"
            "### 问题级别\n"
            "P1\n"
        ),
    )

    rules = service.build_rules(document)

    assert len(rules) == 1
    rule = rules[0]
    assert rule.rule_id == "PERF-POOL-001"
    assert rule.priority == "P1"
    assert rule.applicable_languages == ["java"]
    assert rule.level_one_scene == "并发与线程池"
    assert rule.level_two_scene == "线程池容量配置"
    assert rule.level_three_scene == "线程池扩容缺少容量评估"
    assert "评估下游容量" in rule.description
    assert "executor.setMaxPoolSize(512);" in rule.problem_code_line
    assert "executor.setMaxPoolSize(32)" in rule.false_positive_code


def test_knowledge_ingestion_persists_review_rules(storage_root: Path) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    document = ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-SQL-001 SQL 结果集过大必须分页\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n查询性能\n\n"
                "### 三级场景\n大结果集分页缺失\n\n"
                "### 描述\n检查数据库查询是否做分页限制。\n\n"
                "### 问题代码示例\n```java\nfindAll();\n```\n\n"
                "### 问题代码行\nfindAll();\n\n"
                "### 误报代码\n```java\nfindAll(PageRequest.of(0, 50));\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        )
    )

    rules = SqliteKnowledgeRuleRepository(storage_root / "app.db").list_for_document_ids([document.doc_id])

    assert rules
    assert rules[0].rule_id == "PERF-SQL-001"
    assert rules[0].expert_id == "performance_reliability"
    assert rules[0].level_three_scene == "大结果集分页缺失"


def test_real_product_rule_document_parses_with_new_template() -> None:
    service = KnowledgeRuleIndexService()
    document = KnowledgeDocument(
        title="性能与可靠性真实规则集",
        expert_id="performance_reliability",
        doc_type="review_rule",
        source_filename="performance-reliability-real-rules.md",
        content=REAL_PERF_RULES_PATH.read_text(encoding="utf-8"),
    )

    rules = service.build_rules(document)

    assert len(rules) >= 8
    first_rule = rules[0]
    assert first_rule.rule_id == "PERF-POOL-001"
    assert first_rule.level_one_scene == "数据库访问"
    assert first_rule.level_two_scene == "连接池配置"
    assert first_rule.level_three_scene == "连接池扩容缺少容量评估"
    assert first_rule.priority == "P1"
    assert first_rule.language == "java"
    assert "config.setMaximumPoolSize(256);" in first_rule.problem_code_example
    assert "config.setMaximumPoolSize(256);" in first_rule.problem_code_line
