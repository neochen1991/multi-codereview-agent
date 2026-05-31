from app.domain.models.expert_profile import ExpertProfile
from app.domain.models import report as report_models
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.change_impact_report_service import ChangeImpactReportService
from app.services.llm_chat_service import LLMResolution, LLMTextResult


def _expert() -> ExpertProfile:
    return ExpertProfile(
        expert_id="change_impact_analysis",
        name="change-impact-analysis",
        name_zh="关联性影响分析专家",
        role="impact",
        system_prompt="你是关联影响分析专家。",
        provider="openai",
        model="fake-model",
        api_base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )


def _report() -> report_models.ImpactReport:
    return report_models.ImpactReport(
        graph_status="ready",
        changed_files=[
            "src/main/java/com/example/order/OrderController.java",
            "src/main/java/com/example/order/OrderRepository.java",
        ],
        changed_symbols=[
            report_models.ImpactSymbol(
                file_path="src/main/java/com/example/order/OrderController.java",
                symbol="notifyAudit",
                kind="function",
                container="OrderController",
                line_start=9,
            ),
            report_models.ImpactSymbol(
                file_path="src/main/java/com/example/order/OrderApplicationService.java",
                symbol="createOrder",
                kind="function",
                container="OrderApplicationService",
                line_start=24,
            ),
        ],
        impacted_files=[
            report_models.ImpactFile(
                file_path="src/test/java/com/example/order/OrderControllerTest.java",
                relationship="test_candidate",
                reason="入口链路测试建议回归",
                risk_level="medium",
            ),
            report_models.ImpactFile(
                file_path="src/main/java/com/example/order/OrderRepository.java",
                relationship="data_access",
                reason="订单保存逻辑受影响",
                risk_level="high",
            ),
        ],
        impacted_modules=["order"],
        impact_paths=[
            report_models.ImpactPath(
                source="OrderController",
                target="OrderRepository",
                path=["OrderController", "OrderApplicationService", "OrderRepository"],
                depth=2,
                risk="high",
            )
        ],
        external_entrypoints=["audit-topic", "order-api"],
        risk_level="high",
        recommended_test_scope=[
            report_models.TestScopeRecommendation(
                scope="接口级回归测试",
                reason="入口层有变更",
                paths=["src/test/java/com/example/order/OrderControllerTest.java"],
                priority="high",
            )
        ],
        manual_verification=["人工确认审计发布是否影响下游消费。"],
    )


def test_change_impact_report_service_synthesizes_llm_fields():
    service = ChangeImpactReportService()
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )
    service._llm.complete_text = lambda **kwargs: LLMTextResult(  # type: ignore[method-assign]
        text=(
            '{"summary":"本次改动影响订单入口和审计发布链路。",'
            '"key_impact_points":["OrderController 入口新增 notifyAudit。"],'
            '"test_focus":["优先回归订单创建接口。"],'
            '"manual_checks":["确认审计发布是否影响下游消费。"],'
            '"markdown":"## 结论\\n本次改动影响订单入口和审计发布链路。"}'
        ),
        mode="live",
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )

    updated, llm_result = service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(),
        report=_report(),
        trace={"repo": "repo", "detect_changes": {}, "context_results": [], "impact_results": []},
        review_id="rev_test",
    )

    assert llm_result is not None
    assert updated.report_summary == "本次改动影响订单入口和审计发布链路。"
    assert updated.key_impact_points == ["OrderController 入口新增 notifyAudit。"]
    assert updated.test_focus == ["优先回归订单创建接口。"]
    assert "优先回归订单创建接口。" in updated.llm_markdown
    assert "OrderController | notifyAudit |" in updated.llm_markdown
    assert "分析时间：" in updated.llm_markdown
    assert updated.llm_generated is True
    assert updated.analysis_workflow


def test_change_impact_report_service_skips_llm_in_thorough_light_mode():
    service = ChangeImpactReportService()
    calls = {"count": 0}
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )

    def _complete_text(**kwargs):  # noqa: ANN001
        calls["count"] += 1
        return LLMTextResult(
            text="{}",
            mode="live",
            provider="openai",
            model="fake-model",
            base_url="https://example.com",
            api_key_env="FAKE_KEY",
        )

    service._llm.complete_text = _complete_text  # type: ignore[method-assign]

    updated, llm_result = service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(review_quality_mode="thorough_review", default_analysis_mode="light"),
        report=_report(),
        trace={"repo": "repo", "detect_changes": {}, "context_results": [], "impact_results": []},
        review_id="rev_test",
    )

    assert calls["count"] == 0
    assert llm_result is not None
    assert llm_result.mode == "fallback"
    assert llm_result.error == "skipped:fact_template_only_for_thorough_light_review"
    assert updated.llm_generated is False
    assert updated.key_impact_points


def test_change_impact_report_service_compacts_noisy_graph_facts():
    service = ChangeImpactReportService()
    captured: dict[str, str] = {}
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )

    def _complete_text(**kwargs):
        captured["user_prompt"] = str(kwargs.get("user_prompt") or "")
        return LLMTextResult(
            text='{"summary":"ok","key_impact_points":[],"test_focus":[],"manual_checks":[],"markdown":""}',
            mode="live",
            provider="openai",
            model="fake-model",
            base_url="https://example.com",
            api_key_env="FAKE_KEY",
        )

    service._llm.complete_text = _complete_text  # type: ignore[method-assign]

    service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(),
        report=_report(),
        trace={
            "repo": "repo",
            "detect_changes": {"affected_processes": [{"name": "raw-process"}], "summary": {"direct": 1}},
            "context_results": [{"target": {"name": "consume"}, "byDepth": {"1": ["raw"]}, "callers": ["Command"]}],
            "impact_results": [
                {
                    "target": {"name": "consume"},
                    "affected_processes": [{"name": "raw-process"}],
                    "byDepth": {"1": ["raw-depth"]},
                    "summary": {"direct": 1},
                }
            ],
        },
        review_id="rev_test",
    )

    prompt = captured["user_prompt"]
    assert "byDepth" not in prompt
    assert "affected_processes" not in prompt
    assert "raw-depth" not in prompt


def test_change_impact_report_service_falls_back_when_llm_output_not_json():
    service = ChangeImpactReportService()
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )
    service._llm.complete_text = lambda **kwargs: LLMTextResult(  # type: ignore[method-assign]
        text="plain text response",
        mode="fallback",
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
        error="parse_failed",
    )

    updated, _ = service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(),
        report=_report(),
        trace={"repo": "repo", "detect_changes": {}, "context_results": [], "impact_results": []},
        review_id="rev_test",
    )

    assert updated.report_summary
    assert updated.key_impact_points
    assert updated.llm_markdown
    assert updated.llm_generated is False


def test_change_impact_report_service_degrades_to_fact_report_when_llm_times_out():
    service = ChangeImpactReportService()
    captured: dict[str, object] = {}
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )

    def fail_with_timeout(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("request_timeout: impact report llm timed out")

    service._llm.complete_text = fail_with_timeout  # type: ignore[method-assign]

    updated, llm_result = service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(),
        report=_report(),
        trace={
            "repo": "repo",
            "source_branch": "feature/order-impact",
            "target_branch": "main",
            "detect_changes": {},
            "context_results": [],
            "impact_results": [],
        },
        review_id="rev_test",
    )

    assert captured["timeout_seconds"] == 45.0
    assert llm_result is not None
    assert llm_result.mode == "fallback"
    assert "request_timeout" in llm_result.error
    assert updated.llm_generated is False
    assert updated.llm_markdown
    assert "feature/order-impact -> main" in updated.llm_markdown
    assert "OrderController -> OrderApplicationService -> OrderRepository" in updated.llm_markdown
    assert any("LLM" in item and "已改用" in item for item in updated.limitations)


def test_change_impact_report_service_supports_schema_driven_custom_placeholder():
    service = ChangeImpactReportService()
    service._report_template = "# 自定义报告\n\n## 风险摘要\n{{custom_risk_summary}}\n\n## 结论\n{{summary}}"
    service._template_schema = {
        "variables": [
            {"name": "custom_risk_summary", "source": "llm", "required": True, "description": "自定义风险摘要"},
            {"name": "summary", "source": "llm", "required": True, "description": "总结"},
        ]
    }
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )
    service._llm.complete_text = lambda **kwargs: LLMTextResult(  # type: ignore[method-assign]
        text=(
            '{"summary":"本次改动影响订单创建主链路。",'
            '"key_impact_points":["订单入口和仓储链路被波及。"],'
            '"test_focus":["优先回归订单创建接口。"],'
            '"manual_checks":["确认消息发送顺序。"],'
            '"template_variables":{"custom_risk_summary":"订单入口到仓储链路属于高风险变更。"}}'
        ),
        mode="live",
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )

    updated, _ = service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(),
        report=_report(),
        trace={"repo": "repo", "detect_changes": {}, "context_results": [], "impact_results": []},
        review_id="rev_test",
    )

    assert "订单入口到仓储链路属于高风险变更。" in updated.llm_markdown
    assert "本次改动影响订单创建主链路。" in updated.llm_markdown


def test_change_impact_report_service_allows_llm_to_fill_non_llm_schema_variables():
    service = ChangeImpactReportService()
    service._report_template = "# 自定义报告\n\n仓库：{{repo_name}}\n\n链路：{{impact_paths}}"
    service._template_schema = {
        "variables": [
            {"name": "repo_name", "source": "system", "required": True, "description": "仓库名称"},
            {"name": "impact_paths", "source": "gitnexus", "required": False, "description": "关键调用链路"},
        ]
    }
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )
    service._llm.complete_text = lambda **kwargs: LLMTextResult(  # type: ignore[method-assign]
        text=(
            '{"summary":"影响订单主链路。",'
            '"key_impact_points":["订单创建链路被波及。"],'
            '"test_focus":["优先回归订单创建接口。"],'
            '"manual_checks":["确认消息发送顺序。"],'
            '"template_variables":{"repo_name":"order-domain-service","impact_paths":"- OrderController.createOrder -> OrderApplicationService.createOrder -> OrderRepository.save"}}'
        ),
        mode="live",
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )

    updated, _ = service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(),
        report=_report(),
        trace={"repo": "repo", "detect_changes": {}, "context_results": [], "impact_results": []},
        review_id="rev_test",
    )

    assert "order-domain-service" in updated.llm_markdown
    assert "OrderController.createOrder -> OrderApplicationService.createOrder -> OrderRepository.save" in updated.llm_markdown


def test_change_impact_report_service_renders_intranet_template_with_server_side_values():
    service = ChangeImpactReportService()
    service._report_template = "## 模板标题\n\n{{ 当前时间 }}\n\n{{ 遍历 java_methods，每行一条记录 }}"
    service._template_schema = {
        "variables": [
            {"name": "当前时间", "source": "llm", "required": True, "description": "分析时间"},
            {"name": "遍历 java_methods，每行一条记录", "source": "llm", "required": True, "description": "方法列表"},
        ]
    }
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )
    service._llm.complete_text = lambda **kwargs: LLMTextResult(  # type: ignore[method-assign]
        text=(
            '{"summary":"订单主链路受影响。",'
            '"key_impact_points":["订单入口到仓储链路需要回归。"],'
            '"test_focus":["优先回归订单创建接口。"],'
            '"manual_checks":["确认审计消息发送链路。"],'
            '"markdown":"## 代码关联影响分析报告\\n\\n> 分析时间：2026-04-28 10:00:00\\n\\n### 变更方法清单\\n\\n| 类名 | 方法 | 变更类型 | 文件 |\\n| --- | --- | --- | --- |\\n| OrderController | notifyAudit | modified | src/main/java/com/example/order/OrderController.java |"}'
        ),
        mode="live",
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )

    updated, _ = service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(),
        report=_report(),
        trace={"repo": "repo", "detect_changes": {}, "context_results": [], "impact_results": []},
        review_id="rev_test",
    )

    assert updated.llm_markdown.startswith("## 模板标题")
    assert "{{ 当前时间 }}" not in updated.llm_markdown
    assert "{{ 遍历 java_methods，每行一条记录 }}" not in updated.llm_markdown
    assert "2026-04-28 10:00:00" not in updated.llm_markdown
    assert "OrderController | notifyAudit | modified" in updated.llm_markdown


def test_change_impact_report_service_can_analyze_template_schema_with_llm():
    service = ChangeImpactReportService()
    service._llm.complete_text = lambda **kwargs: LLMTextResult(  # type: ignore[method-assign]
        text=(
            '{"variables":['
            '{"name":"summary","source":"llm","required":true,"description":"总结","format":"markdown"},'
            '{"name":"impact_paths","source":"gitnexus","required":false,"description":"调用链路","format":"bullet_list"}'
            ']}'
        ),
        mode="live",
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )

    schema_payload = service.analyze_template_schema(
        "# 报告\n\n{{summary}}\n\n{{impact_paths}}",
        RuntimeSettings(),
    )

    variables = list(schema_payload.get("variables") or [])
    assert variables[0]["name"] == "summary"
    assert variables[0]["source"] == "llm"
    assert variables[1]["name"] == "impact_paths"
    assert variables[1]["source"] == "gitnexus"


def test_extract_template_placeholders_supports_chinese_instructional_placeholders():
    placeholders = ChangeImpactReportService.extract_template_placeholders(
        "## 模板\n{{ 当前时间 }}\n{{ 遍历 java_methods，每行一条记录 }}\n{{ source_branch }}"
    )

    assert placeholders == ["当前时间", "遍历 java_methods，每行一条记录", "source_branch"]


def test_change_impact_report_service_renders_intranet_template_without_leftover_placeholders():
    service = ChangeImpactReportService()
    service._report_template = """## 代码关联影响分析报告

> 分析时间：{{ 当前时间 }}
> 对比分支：{{ source_branch }} -> {{ target_branch }}

### 变更方法清单

{{ 遍历 java_methods，每行一条记录 }}

### 调用链影响分析

{{ 遍历 call_chains，每个方法生成以下块 }}
#### `{{ method_signature }}`
{{ 列出 callers，无则显示 “无上游调用”}}
{{ 列出 callees，无则显示 “无下游调用”}}
{{ 基于调用链分析，简要说明该方法变更可能带来的影响 }}
{{ 结束遍历 }}

### 风险评估
{{ 列出高风险项：如修改了被多处调用的核心方法 }}

### 测试建议
{{ 根据调用链和变更类型，列出需要重点测试的场景 }}
"""
    service._llm.resolve_expert = lambda expert, runtime: LLMResolution(  # type: ignore[method-assign]
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
    )
    service._llm.complete_text = lambda **kwargs: LLMTextResult(  # type: ignore[method-assign]
        text="plain text response",
        mode="fallback",
        provider="openai",
        model="fake-model",
        base_url="https://example.com",
        api_key_env="FAKE_KEY",
        error="parse_failed",
    )

    updated, _ = service.synthesize(
        expert=_expert(),
        runtime_settings=RuntimeSettings(),
        report=_report(),
        trace={
            "repo": "repo",
            "source_branch": "feature/order-impact",
            "target_branch": "main",
            "detect_changes": {},
            "context_results": [],
            "impact_results": [],
        },
        review_id="rev_test",
    )

    assert "{{" not in updated.llm_markdown
    assert "feature/order-impact -> main" in updated.llm_markdown
    assert "OrderController | notifyAudit | modified" in updated.llm_markdown
    assert "OrderController -> OrderApplicationService -> OrderRepository" in updated.llm_markdown or "OrderController -> OrderApplicationService" in updated.llm_markdown
