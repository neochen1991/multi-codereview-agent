from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

from app.domain.models.runtime_settings import RuntimeSettings


def _load_tool_module():
    tool_path = Path("extensions/tools/design_spec_alignment/run.py")
    spec = importlib.util.spec_from_file_location("design_spec_alignment_run", tool_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_design_spec_alignment_uses_llm_to_parse_documents(monkeypatch):
    module = _load_tool_module()

    class StubResult:
        text = """
        {
          "document_title": "订单创建详细设计",
          "business_goal": "为新订单创建接口补充一致的入参与出参约束",
          "api_definitions": [{"name":"创建订单","method":"POST","path":"/api/orders","purpose":"创建订单","source_quote":"POST /api/orders"}],
          "request_fields": [{"name":"userId","location":"body","field_type":"string","required":"required","description":"用户ID","source_quote":"userId string"}],
          "response_fields": [{"name":"createdAt","location":"response","field_type":"string","required":"required","description":"创建时间","source_quote":"createdAt"}],
          "table_definitions": [{"table_name":"orders","fields":["id","user_id"],"constraints":["not null"],"indexes":["idx_orders_user_id"],"source_quote":"orders"}],
          "business_sequences": [{"step":"1","actor":"API","action":"写入订单","expected_result":"订单创建成功","source_quote":"写入订单表"}],
          "performance_requirements": [{"title":"接口耗时","requirement":"P95 小于 200ms","source_quote":"P95 < 200ms"}],
          "security_requirements": [{"title":"鉴权","requirement":"必须校验用户 token","source_quote":"校验 token"}],
          "unknown_or_ambiguous_points": ["库存扣减时机未明确"]
        }
        """

    calls: list[dict[str, object]] = []

    class StubLLMService:
        def resolve_main_agent(self, runtime):
            return object()

        def complete_text(self, **kwargs):
            calls.append(kwargs)
            return StubResult()

    monkeypatch.setattr(module, "LLMChatService", StubLLMService)

    structured = module._parse_design_docs_with_llm(
        [
            {
                "title": "订单创建详细设计",
                "content": "# API\nPOST /api/orders\n# 请求参数\n- userId string",
            }
        ],
        RuntimeSettings(),
    )

    assert structured.document_title == "订单创建详细设计"
    assert structured.api_definitions[0].path == "/api/orders"
    assert structured.response_fields[0].name == "createdAt"
    assert calls and calls[0]["temperature"] == 0.0


def test_design_spec_alignment_fails_when_llm_returns_invalid_json(monkeypatch):
    module = _load_tool_module()

    class StubResult:
        text = "not-json"

    class StubLLMService:
        def resolve_main_agent(self, runtime):
            return object()

        def complete_text(self, **kwargs):
            return StubResult()

    monkeypatch.setattr(module, "LLMChatService", StubLLMService)

    try:
        module._parse_design_docs_with_llm(
            [{"title": "设计", "content": "# API\nPOST /api/orders"}],
            RuntimeSettings(),
        )
    except RuntimeError as exc:
        assert "未返回合法 JSON" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected runtime error")


def test_design_spec_alignment_treats_nonfunctional_requirements_as_uncertain():
    module = _load_tool_module()

    structured = module.StructuredDesignDoc(
        document_title="设计",
        api_definitions=[
            module.DesignAPI(method="GET", path="/api/orders", purpose="查询订单"),
        ],
        response_fields=[
            module.DesignField(name="createdAt", field_type="string", description="创建时间"),
        ],
        performance_requirements=[
            module.DesignRequirement(title="接口耗时", requirement="P95 小于 200ms"),
        ],
        security_requirements=[
            module.DesignRequirement(title="鉴权", requirement="必须校验用户 token"),
        ],
    )

    requirements = module._flatten_design_requirements(structured)
    matched, missing, _ = module._compare_requirements(
        requirements,
        "GET /api/orders createdAt string",
    )
    uncertain = module._collect_nonfunctional_observations(
        structured,
        "GET /api/orders createdAt string",
    )

    assert any("GET /api/orders" in item for item in matched)
    assert not any("P95" in item for item in missing)
    assert not any("token" in item for item in missing)
    assert any("性能要求待专项验证" in item for item in uncertain)
    assert any("安全要求待专项验证" in item for item in uncertain)


def test_design_spec_alignment_returns_failure_payload_when_llm_parse_fails(monkeypatch):
    module = _load_tool_module()

    def _raise(*args, **kwargs):
        raise RuntimeError("请求超时")

    monkeypatch.setattr(module, "_parse_design_docs_with_llm", _raise)
    monkeypatch.setattr(
        module.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "design_docs": [{"title": "设计", "content": "# API\nPOST /api/orders"}],
                    "runtime": RuntimeSettings().model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        ),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", stdout)

    exit_code = module.main()
    payload = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert payload["success"] is False
    assert payload["design_alignment_status"] == "insufficient_design_context"
    assert "解析失败" in payload["summary"]
    assert payload["parse_failed"] is True
