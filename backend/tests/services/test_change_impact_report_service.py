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
    assert updated.llm_markdown.startswith("# 关联影响分析报告")
    assert "## 1. 报告结论" in updated.llm_markdown
    assert "## 5. 数据与事务影响" in updated.llm_markdown
    assert "Repository" in updated.llm_markdown
    assert "高" in updated.llm_markdown or "high" in updated.llm_markdown
    assert "本次改动影响订单入口和审计发布链路。" in updated.llm_markdown
    assert updated.llm_generated is True
    assert updated.analysis_workflow


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
