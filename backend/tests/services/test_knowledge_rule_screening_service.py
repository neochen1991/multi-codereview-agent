from pathlib import Path

import logging

from app.domain.models.knowledge import KnowledgeDocument
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMTextResult
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_rule_index_service import KnowledgeRuleIndexService
from app.services.knowledge_rule_screening_service import KnowledgeRuleScreeningService
from app.services.knowledge_service import KnowledgeService

REAL_PERF_RULES_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "expert-specs-export"
    / "performance_reliability"
    / "performance-reliability-real-rules.md"
)


def test_knowledge_service_bootstraps_builtin_java_ddd_rules(storage_root: Path) -> None:
    service = KnowledgeService(storage_root)
    imported = service.bootstrap_builtin_documents()

    assert imported > 0

    security = service.screen_rules_for_expert(
        "security_compliance",
        {
            "changed_files": ["src/main/java/com/acme/order/interfaces/OrderController.java"],
            "query_terms": ["RestController", "PreAuthorize", "CloseOrderCommand", "tenant"],
            "focus_file": "src/main/java/com/acme/order/interfaces/OrderController.java",
        },
    )
    assert any(item["rule_id"] == "SEC-JDDD-001" for item in security["matched_rules_for_llm"])

    performance = service.screen_rules_for_expert(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/order/app/OrderApplicationService.java"],
            "query_terms": ["Transactional", "FeignClient", "Kafka", "publish"],
            "focus_file": "src/main/java/com/acme/order/app/OrderApplicationService.java",
        },
    )
    assert any(item["rule_id"] == "PERF-JDDD-001" for item in performance["matched_rules_for_llm"])

    architecture = service.screen_rules_for_expert(
        "architecture_design",
        {
            "changed_files": ["src/main/java/com/acme/order/interfaces/OrderController.java"],
            "query_terms": ["Controller", "Repository", "Mapper"],
            "focus_file": "src/main/java/com/acme/order/interfaces/OrderController.java",
        },
    )
    assert any(item["rule_id"] == "ARCH-JDDD-001" for item in architecture["matched_rules_for_llm"])

    ddd = service.screen_rules_for_expert(
        "ddd_specification",
        {
            "changed_files": ["src/main/java/com/acme/order/domain/Order.java"],
            "query_terms": ["Aggregate", "setStatus", "DomainEvent", "outbox"],
            "focus_file": "src/main/java/com/acme/order/domain/Order.java",
        },
    )
    assert any(item["rule_id"] == "DDD-JDDD-001" for item in ddd["matched_rules_for_llm"])


def test_knowledge_rule_screening_service_skips_ddd_strong_rules_for_general_java(storage_root: Path) -> None:
    service = KnowledgeService(storage_root)
    service.bootstrap_builtin_documents()

    architecture = service.screen_rules_for_expert(
        "architecture_design",
        {
            "changed_files": ["src/main/java/com/acme/web/OwnerController.java"],
            "query_terms": [
                "java_mode:general",
                "java_signal:controller_entry",
                "Controller",
                "Repository",
            ],
            "focus_file": "src/main/java/com/acme/web/OwnerController.java",
        },
    )

    matched_rule_ids = {item["rule_id"] for item in architecture["matched_rules_for_llm"]}
    assert "ARCH-JDDD-001" not in matched_rule_ids
    skipped_rule = next(
        item
        for item in architecture["sample_no_hit_rules"]
        if item["rule_id"] == "ARCH-JDDD-001"
    )
    assert skipped_rule["reason"] == "当前 Java 审查模式下不启用该类 DDD 强约束规则"


def test_knowledge_rule_screening_service_traverses_all_rules(storage_root: Path) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n连接池配置\n\n"
                "### 三级场景\n连接池扩容缺少容量评估\n\n"
                "### 描述\n检查连接池扩容是否同步评估下游容量，并关注 maximumPoolSize 与 datasource 容量。\n\n"
                "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
                "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
                "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n\n"
                "## RULE: PERF-CACHE-001 大缓存对象必须评估序列化开销\n\n"
                "### 一级场景\n缓存\n\n"
                "### 二级场景\n序列化\n\n"
                "### 三级场景\n大对象缓存缺少体积评估\n\n"
                "### 描述\n检查缓存对象是否过大。\n\n"
                "### 问题代码示例\n```java\ncache.put(key, payload);\n```\n\n"
                "### 问题代码行\ncache.put(key, payload);\n\n"
                "### 误报代码\n```java\ncache.put(key, summary);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP2\n"
            ),
        )
    )

    service = KnowledgeRuleScreeningService(storage_root)
    result = service.screen(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/HikariConfig.java"],
            "query_terms": ["hikari", "maximumPoolSize", "datasource"],
            "focus_file": "src/main/java/com/acme/HikariConfig.java",
        },
    )

    assert result["total_rules"] == 2
    assert result["matched_rule_count"] == 1
    assert result["must_review_count"] == 0
    assert result["possible_hit_count"] == 1
    matched_rules = result["matched_rules_for_llm"]
    assert matched_rules and matched_rules[0]["rule_id"] == "PERF-POOL-001"


def test_knowledge_rule_screening_service_logs_summary(storage_root: Path, caplog) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n连接池配置\n\n"
                "### 三级场景\n连接池扩容缺少容量评估\n\n"
                "### 描述\n检查连接池扩容是否同步评估下游容量，并关注 maximumPoolSize 与 datasource 容量。\n\n"
                "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
                "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
                "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        )
    )
    service = KnowledgeRuleScreeningService(storage_root)

    with caplog.at_level(logging.INFO):
        service.screen(
            "performance_reliability",
            {
                "changed_files": ["src/main/java/com/acme/HikariConfig.java"],
                "query_terms": ["hikari", "maximumPoolSize", "datasource"],
                "focus_file": "src/main/java/com/acme/HikariConfig.java",
            },
        )

    assert "knowledge rule screening expert_id=performance_reliability" in caplog.text
    assert "matched_rule_ids=['PERF-POOL-001']" in caplog.text


def test_knowledge_rule_screening_service_can_use_llm(storage_root: Path, monkeypatch) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n连接池配置\n\n"
                "### 三级场景\n连接池扩容缺少容量评估\n\n"
                "### 描述\n检查连接池扩容是否同步评估下游容量，并关注 maximumPoolSize 与 datasource 容量。\n\n"
                "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
                "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
                "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n\n"
                "## RULE: PERF-CACHE-001 大缓存对象必须评估序列化开销\n\n"
                "### 一级场景\n缓存\n\n"
                "### 二级场景\n序列化\n\n"
                "### 三级场景\n大对象缓存缺少体积评估\n\n"
                "### 描述\n检查缓存对象是否过大。\n\n"
                "### 问题代码示例\n```java\ncache.put(key, payload);\n```\n\n"
                "### 问题代码行\ncache.put(key, payload);\n\n"
                "### 误报代码\n```java\ncache.put(key, summary);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP2\n"
            ),
        )
    )
    service = KnowledgeRuleScreeningService(storage_root)

    monkeypatch.setattr(
        service._llm,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text=
                '{"rules":['
                '{"rule_id":"PERF-POOL-001","decision":"must_review","reason":"MR 直接修改了连接池容量参数","matched_terms":["hikari","maximumPoolSize"],"matched_signals":["semantic:pool"]},'
                '{"rule_id":"PERF-CACHE-001","decision":"no_hit","reason":"当前改动与缓存无关","matched_terms":[],"matched_signals":[]}'
                "]}",
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )

    result = service.screen(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/HikariConfig.java"],
            "query_terms": ["hikari", "maximumPoolSize", "datasource"],
            "focus_file": "src/main/java/com/acme/HikariConfig.java",
        },
        runtime_settings=RuntimeSettings(rule_screening_mode="llm", rule_screening_batch_size=8),
        analysis_mode="standard",
        review_id="rev_llm_screen",
    )

    assert result["screening_mode"] == "llm"
    assert result["screening_fallback_used"] is False
    assert result["must_review_count"] == 1
    assert result["matched_rules_for_llm"][0]["rule_id"] == "PERF-POOL-001"
    assert len(result["batch_summaries"]) == 1
    assert result["batch_summaries"][0]["batch_index"] == 1
    assert result["batch_summaries"][0]["input_rule_count"] == 2
    assert result["total_elapsed_ms"] >= 0
    assert result["batch_summaries"][0]["llm"]["elapsed_ms"] >= 0


def test_knowledge_rule_screening_service_can_parse_llm_json_wrapped_in_text(storage_root: Path, monkeypatch) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n连接池配置\n\n"
                "### 三级场景\n连接池扩容缺少容量评估\n\n"
                "### 描述\n检查连接池扩容是否同步评估下游容量，并关注 maximumPoolSize 与 datasource 容量。\n\n"
                "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
                "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
                "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        )
    )
    service = KnowledgeRuleScreeningService(storage_root)

    monkeypatch.setattr(
        service._llm,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text=(
                "下面是筛选结果，请只取 JSON:\n"
                '{"rules":[{"rule_id":"PERF-POOL-001","decision":"must_review","reason":"MR 直接修改了连接池容量参数","matched_terms":["hikari","maximumPoolSize"],"matched_signals":["semantic:pool"]}]}'
            ),
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )

    result = service.screen(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/HikariConfig.java"],
            "query_terms": ["hikari", "maximumPoolSize", "datasource"],
            "focus_file": "src/main/java/com/acme/HikariConfig.java",
        },
        runtime_settings=RuntimeSettings(rule_screening_mode="llm", rule_screening_batch_size=8),
        analysis_mode="standard",
        review_id="rev_llm_screen_wrapped",
    )

    assert result["screening_mode"] == "llm"
    assert result["screening_fallback_used"] is False
    assert result["must_review_count"] == 1
    assert result["matched_rules_for_llm"][0]["rule_id"] == "PERF-POOL-001"


def test_knowledge_rule_screening_service_records_llm_batches(storage_root: Path, monkeypatch) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n连接池配置\n\n"
                "### 三级场景\n连接池扩容缺少容量评估\n\n"
                "### 描述\n检查连接池扩容是否同步评估下游容量，并关注 maximumPoolSize 与 datasource 容量。\n\n"
                "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
                "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
                "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n\n"
                "## RULE: PERF-BATCH-001 批处理写入必须控制批大小与事务范围\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n批处理\n\n"
                "### 三级场景\n批处理事务范围过大\n\n"
                "### 描述\n检查批大小和事务边界。\n\n"
                "### 问题代码示例\n```java\nflush(records);\n```\n\n"
                "### 问题代码行\nflush(records);\n\n"
                "### 误报代码\n```java\nflush(records.subList(0, 100));\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n\n"
                "## RULE: PERF-JSON-001 大型对象序列化路径必须避免重复拷贝\n\n"
                "### 一级场景\n序列化\n\n"
                "### 二级场景\nJSON 热路径\n\n"
                "### 三级场景\n重复序列化拷贝\n\n"
                "### 描述\n检查 JSON 热路径的重复序列化。\n\n"
                "### 问题代码示例\n```java\nserialize(payload);\n```\n\n"
                "### 问题代码行\nserialize(payload);\n\n"
                "### 误报代码\n```java\nserialize(summary);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP2\n\n"
                "## RULE: PERF-HTTP-001 远程调用必须设置超时与隔离策略\n\n"
                "### 一级场景\n远程调用\n\n"
                "### 二级场景\n超时控制\n\n"
                "### 三级场景\n远程调用缺少隔离\n\n"
                "### 描述\n检查远程调用超时配置。\n\n"
                "### 问题代码示例\n```java\nhttpClient.execute(request);\n```\n\n"
                "### 问题代码行\nhttpClient.execute(request);\n\n"
                "### 误报代码\n```java\nhttpClient.withTimeout(1000).execute(request);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n\n"
                "## RULE: PERF-LOCK-001 粗粒度锁必须缩小临界区\n\n"
                "### 一级场景\n并发与线程池\n\n"
                "### 二级场景\n锁粒度\n\n"
                "### 三级场景\n锁内执行重操作\n\n"
                "### 描述\n检查粗锁和长临界区。\n\n"
                "### 问题代码示例\n```java\nsynchronized(lock) { callRemote(); }\n```\n\n"
                "### 问题代码行\nsynchronized(lock) { callRemote(); }\n\n"
                "### 误报代码\n```java\nsynchronized(lock) { counter++; }\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        )
    )
    service = KnowledgeRuleScreeningService(storage_root)
    call_index = {"value": 0}

    def _fake_complete_text(**_kwargs):
        call_index["value"] += 1
        if call_index["value"] == 1:
            text = (
                '{"rules":['
                    '{"rule_id":"PERF-POOL-001","decision":"must_review","reason":"连接池容量变更直接命中","matched_terms":["hikari"],"matched_signals":["semantic:pool"]}'
                    ',{"rule_id":"PERF-BATCH-001","decision":"possible_hit","reason":"批大小控制需要关注","matched_terms":["chunk"],"matched_signals":["semantic:batch"]}'
                    ',{"rule_id":"PERF-JSON-001","decision":"no_hit","reason":"当前未命中 JSON 热路径","matched_terms":[],"matched_signals":[]}'
                    ',{"rule_id":"PERF-HTTP-001","decision":"no_hit","reason":"当前未涉及远程调用","matched_terms":[],"matched_signals":[]}'
                "]}"
            )
        else:
            text = (
                '{"rules":['
                    '{"rule_id":"PERF-LOCK-001","decision":"no_hit","reason":"当前未涉及锁竞争","matched_terms":[],"matched_signals":[]}'
                "]}"
            )
        return LLMTextResult(
            text=text,
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(service._llm, "complete_text", _fake_complete_text)

    result = service.screen(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/HikariBatchConsumer.java"],
            "query_terms": ["hikari", "chunk"],
            "focus_file": "src/main/java/com/acme/HikariBatchConsumer.java",
        },
        runtime_settings=RuntimeSettings(rule_screening_mode="llm", rule_screening_batch_size=1),
        analysis_mode="light",
        review_id="rev_llm_batches",
    )

    assert result["screening_mode"] == "llm"
    assert len(result["batch_summaries"]) == 2
    assert result["batch_summaries"][0]["batch_index"] == 1
    assert result["batch_summaries"][0]["batch_count"] == 2
    assert result["batch_summaries"][0]["decisions"][0]["rule_id"] == "PERF-POOL-001"
    assert result["batch_summaries"][1]["decisions"][0]["rule_id"] == "PERF-LOCK-001"
    assert result["total_elapsed_ms"] >= 0
    assert result["batch_summaries"][0]["llm"]["elapsed_ms"] >= 0
    assert result["batch_summaries"][1]["llm"]["elapsed_ms"] >= 0


def test_knowledge_rule_screening_service_falls_back_when_llm_result_invalid(storage_root: Path, monkeypatch) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n连接池配置\n\n"
                "### 三级场景\n连接池扩容缺少容量评估\n\n"
                "### 描述\n检查连接池扩容是否同步评估下游容量，并关注 maximumPoolSize 与 datasource 容量。\n\n"
                "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
                "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
                "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        )
    )
    service = KnowledgeRuleScreeningService(storage_root)

    monkeypatch.setattr(
        service._llm,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text="not-json",
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )

    result = service.screen(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/HikariConfig.java"],
            "query_terms": ["hikari", "maximumPoolSize", "datasource"],
            "focus_file": "src/main/java/com/acme/HikariConfig.java",
        },
        runtime_settings=RuntimeSettings(rule_screening_mode="llm"),
        analysis_mode="standard",
        review_id="rev_llm_fallback",
    )

    assert result["screening_mode"] == "heuristic"
    assert result["screening_fallback_used"] is True
    assert result["matched_rules_for_llm"][0]["rule_id"] == "PERF-POOL-001"


def test_knowledge_rule_screening_service_falls_back_when_llm_uses_transport_fallback(storage_root: Path, monkeypatch) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n连接池配置\n\n"
                "### 三级场景\n连接池扩容缺少容量评估\n\n"
                "### 描述\n检查连接池扩容是否同步评估下游容量，并关注 maximumPoolSize 与 datasource 容量。\n\n"
                "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
                "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
                "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        )
    )
    service = KnowledgeRuleScreeningService(storage_root)

    monkeypatch.setattr(
        service._llm,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text='{"rules":[]}',
            mode="fallback",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            error="request_timeout",
        ),
    )

    result = service.screen(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/HikariConfig.java"],
            "query_terms": ["hikari", "maximumPoolSize", "datasource"],
            "focus_file": "src/main/java/com/acme/HikariConfig.java",
        },
        runtime_settings=RuntimeSettings(rule_screening_mode="llm"),
        analysis_mode="standard",
        review_id="rev_llm_transport_fallback",
    )

    assert result["screening_mode"] == "heuristic"
    assert result["screening_fallback_used"] is True
    assert result["matched_rules_for_llm"][0]["rule_id"] == "PERF-POOL-001"


def test_knowledge_rule_screening_service_overrides_llm_for_ddd_factory_bypass(storage_root: Path, monkeypatch) -> None:
    service = KnowledgeService(storage_root)
    service.bootstrap_builtin_documents()
    screening = KnowledgeRuleScreeningService(storage_root)

    architecture_rules = screening._repository.list_for_expert("architecture_design")
    architecture = screening._finalize_llm_result(
        "architecture_design",
        {
            "changed_files": ["src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java"],
            "query_terms": [
                "java_mode:ddd_enhanced",
                "java_signal:application_service_layer",
                "java_signal:factory_bypass",
                "java_signal:aggregate_factory_call_removed",
                "java_signal:application_service_direct_instantiation",
                "@@ -15,7 +15,7 @@ public final class CourseCreator {",
                "- |         Course course = Course.create(id, name, duration);",
                "+        Course course = new Course(id, name, duration);",
            ],
            "focus_file": "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
        },
        architecture_rules,
        [
            {"rule_id": "ARCH-JDDD-001", "decision": "no_hit", "reason": "controller not involved", "matched_terms": [], "matched_signals": []},
            {"rule_id": "ARCH-JDDD-002", "decision": "no_hit", "reason": "application service has no domain logic", "matched_terms": [], "matched_signals": []},
        ],
        "rev_arch_ddd_override",
    )

    matched = {item["rule_id"]: item for item in architecture["matched_rules_for_llm"]}
    assert matched["ARCH-JDDD-002"]["decision"] == "possible_hit"
    assert "java_signal:factory_bypass" in matched["ARCH-JDDD-002"]["matched_signals"]

    ddd_rules = screening._repository.list_for_expert("ddd_specification")
    ddd = screening._finalize_llm_result(
        "ddd_specification",
        {
            "changed_files": ["src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java"],
            "query_terms": [
                "java_mode:ddd_enhanced",
                "java_signal:application_service_layer",
                "java_signal:factory_bypass",
                "java_signal:aggregate_factory_call_removed",
                "java_signal:application_service_direct_instantiation",
                "java_signal:domain_event_pull_present",
                "- |         Course course = Course.create(id, name, duration);",
                "+        Course course = new Course(id, name, duration);",
                "eventBus.publish(course.pullDomainEvents())",
            ],
            "focus_file": "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
        },
        ddd_rules,
        [
            {"rule_id": "DDD-JDDD-001", "decision": "no_hit", "reason": "setter not changed", "matched_terms": [], "matched_signals": []},
            {"rule_id": "DDD-JDDD-002", "decision": "no_hit", "reason": "no cross aggregate signal", "matched_terms": [], "matched_signals": []},
        ],
        "rev_ddd_override",
    )

    matched = {item["rule_id"]: item for item in ddd["matched_rules_for_llm"]}
    assert matched["DDD-JDDD-001"]["decision"] == "must_review"
    assert "java_signal:factory_bypass" in matched["DDD-JDDD-001"]["matched_signals"]
    assert matched["DDD-JDDD-002"]["decision"] == "possible_hit"


def test_knowledge_rule_screening_prompt_uses_only_scene_and_description_fields() -> None:
    service = KnowledgeRuleScreeningService(Path("/tmp"))
    rule = KnowledgeDocument(
        title="性能规则",
        expert_id="performance_reliability",
        doc_type="review_rule",
        source_filename="perf-rules.md",
        content=(
            "## RULE: PERF-POOL-001 线程池扩容必须配套容量评估\n\n"
            "### 一级场景\n数据库访问\n\n"
            "### 二级场景\n连接池配置\n\n"
            "### 三级场景\n连接池扩容缺少容量评估\n\n"
            "### 描述\n检查连接池扩容是否同步评估下游容量。\n\n"
            "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
            "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
            "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
            "### 语言\njava\n\n"
            "### 问题级别\nP1\n"
        ),
    )
    parsed_rule = KnowledgeRuleIndexService().build_rules(rule)[0]

    prompt = service._build_llm_screening_user_prompt(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/HikariConfig.java"],
            "query_terms": ["hikari", "maximumPoolSize"],
            "focus_file": "src/main/java/com/acme/HikariConfig.java",
        },
        [parsed_rule],
    )

    assert "level_one_scene=数据库访问" in prompt
    assert "level_two_scene=连接池配置" in prompt
    assert "level_three_scene=连接池扩容缺少容量评估" in prompt
    assert "description=检查连接池扩容是否同步评估下游容量。" in prompt
    assert "config.setMaximumPoolSize(256)" not in prompt


def test_knowledge_rule_screening_uses_java_quality_signals_for_general_java_rules(storage_root: Path) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-SQL-001 大结果集查询必须显式分页或限流\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n查询性能\n\n"
                "### 三级场景\n大结果集分页缺失\n\n"
                "### 描述\n检查查询语义是否被放宽为模糊匹配，或是否缺少分页与 limit 保护。\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n\n"
                "## RULE: PERF-BATCH-001 批处理写入必须控制批大小与事务范围\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n批处理\n\n"
                "### 三级场景\n批处理事务范围过大\n\n"
                "### 描述\n检查批处理或批量消费是否缺少 chunk、分页或 limit 控制。\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        )
    )
    service = KnowledgeRuleScreeningService(storage_root)
    rules = service._repository.list_for_expert("performance_reliability")
    result = service._finalize_llm_result(
        "performance_reliability",
        {
            "changed_files": ["src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java"],
            "query_terms": [
                "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
                "java_mode:general",
                "java_quality:query_semantics_weakened",
                "-        return builder.equal(root.get(filter.field().value()), filter.value().value());",
                '+        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            ],
            "focus_file": "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        },
        rules,
        [
            {"rule_id": "PERF-SQL-001", "decision": "no_hit", "reason": "no pagination concern", "matched_terms": [], "matched_signals": []},
            {"rule_id": "PERF-BATCH-001", "decision": "no_hit", "reason": "no batch concern", "matched_terms": [], "matched_signals": []},
        ],
        "rev_general_java_quality_override",
    )

    matched = {item["rule_id"]: item for item in result["matched_rules_for_llm"]}
    assert matched["PERF-SQL-001"]["decision"] == "possible_hit"
    assert "java_quality:query_semantics_weakened" in matched["PERF-SQL-001"]["matched_signals"]


def test_knowledge_rule_screening_uses_java_quality_signals_for_unbounded_query(storage_root: Path) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规则",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="perf-rules.md",
            content=(
                "## RULE: PERF-SQL-001 大结果集查询必须显式分页或限流\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n查询性能\n\n"
                "### 三级场景\n大结果集分页缺失\n\n"
                "### 描述\n检查查询语义是否被放宽为模糊匹配，或是否缺少分页与 limit 保护。\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n\n"
                "## RULE: PERF-BATCH-001 批处理写入必须控制批大小与事务范围\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n批处理\n\n"
                "### 三级场景\n批处理事务范围过大\n\n"
                "### 描述\n检查批处理或批量消费是否缺少 chunk、分页或 limit 控制。\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        )
    )
    service = KnowledgeRuleScreeningService(storage_root)
    rules = service._repository.list_for_expert("performance_reliability")
    result = service._finalize_llm_result(
        "performance_reliability",
        {
            "changed_files": ["src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"],
            "query_terms": [
                "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
                "java_mode:general",
                "java_quality:unbounded_query_risk",
                '-\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk"',
                '+\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC"',
            ],
            "focus_file": "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        },
        rules,
        [
            {"rule_id": "PERF-SQL-001", "decision": "no_hit", "reason": "not enough evidence", "matched_terms": [], "matched_signals": []},
            {"rule_id": "PERF-BATCH-001", "decision": "no_hit", "reason": "not enough evidence", "matched_terms": [], "matched_signals": []},
        ],
        "rev_general_java_limit_override",
    )

    matched = {item["rule_id"]: item for item in result["matched_rules_for_llm"]}
    assert matched["PERF-SQL-001"]["decision"] == "must_review"
    assert "java_quality:unbounded_query_risk" in matched["PERF-SQL-001"]["matched_signals"]


def test_real_product_rule_document_can_be_ingested_and_screened(storage_root: Path) -> None:
    ingestion = KnowledgeIngestionService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能与可靠性真实规则集",
            expert_id="performance_reliability",
            doc_type="review_rule",
            source_filename="performance-reliability-real-rules.md",
            content=REAL_PERF_RULES_PATH.read_text(encoding="utf-8"),
        )
    )

    service = KnowledgeRuleScreeningService(storage_root)
    result = service.screen(
        "performance_reliability",
        {
            "changed_files": ["src/main/java/com/acme/HikariConfig.java"],
            "query_terms": ["hikari", "maximumPoolSize", "setMaximumPoolSize", "connectionTimeout", "datasource"],
            "focus_file": "src/main/java/com/acme/HikariConfig.java",
        },
    )

    assert result["total_rules"] >= 8
    assert result["matched_rule_count"] >= 1
    matched_ids = [item["rule_id"] for item in result["matched_rules_for_llm"]]
    assert "PERF-POOL-001" in matched_ids
