from pathlib import Path

import logging

from app.domain.models.knowledge import KnowledgeDocument
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMTextResult
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_rule_index_service import KnowledgeRuleIndexService
from app.services.knowledge_rule_screening_service import KnowledgeRuleScreeningService

REAL_PERF_RULES_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "expert-specs-export"
    / "performance_reliability"
    / "performance-reliability-real-rules.md"
)


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
