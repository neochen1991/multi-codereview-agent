from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.tool_gateway import ReviewToolGateway


def test_tool_gateway_can_invoke_design_spec_alignment_plugin(storage_root: Path):
    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="correctness-business",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        runtime_tool_bindings=[],
        system_prompt="prompt",
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/demo",
        target_ref="main",
        changed_files=["apps/api/order/service.ts"],
        unified_diff="diff --git a/apps/api/order/service.ts b/apps/api/order/service.ts",
    )

    gateway._invoke_plugin_tool = lambda tool_name, payload: {  # type: ignore[method-assign]
        "success": True,
        "summary": "已解析详细设计文档",
        "design_doc_titles": ["订单详细设计"],
        "structured_design": {"api_definitions": ["POST /api/orders"]},
    }

    results = gateway.invoke_for_expert(
        expert,
        subject,
        RuntimeSettings(runtime_tool_allowlist=["design_spec_alignment"]),
        file_path="apps/api/order/service.ts",
        line_start=12,
        related_files=[],
        design_docs=[
            {
                "doc_id": "design_1",
                "title": "订单详细设计",
                "filename": "order-design.md",
                "content": "# 订单详细设计\n\n## API\n- POST /api/orders",
                "doc_type": "design_spec",
            }
        ],
        extra_tools=["design_spec_alignment"],
    )

    result = next(item for item in results if item["tool_name"] == "design_spec_alignment")
    assert result["success"] is True
    assert result["design_doc_titles"] == ["订单详细设计"]
    assert "POST /api/orders" in " ".join(result["structured_design"]["api_definitions"])


def test_tool_gateway_preserves_plugin_failure_result(storage_root: Path, monkeypatch):
    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="correctness-business",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        runtime_tool_bindings=[],
        system_prompt="prompt",
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/demo",
        target_ref="main",
    )

    monkeypatch.setattr(
        gateway,
        "_invoke_plugin_tool",
        lambda tool_name, payload: {"summary": "详细设计文档解析失败：请求超时", "success": False, "timed_out": True},
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        RuntimeSettings(runtime_tool_allowlist=["design_spec_alignment"]),
        file_path="apps/api/order/service.ts",
        line_start=12,
        extra_tools=["design_spec_alignment"],
    )

    result = next(item for item in results if item["tool_name"] == "design_spec_alignment")
    assert result["success"] is False
    assert result["timed_out"] is True


def test_tool_gateway_skips_plugin_when_bound_skill_not_active(storage_root: Path):
    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="correctness-business",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        runtime_tool_bindings=[],
        system_prompt="prompt",
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/demo",
        target_ref="main",
    )

    class Plugin:
        runtime = "python"
        tool_path = str(storage_root)
        entry = "run.py"
        timeout_seconds = 60
        allowed_experts = ["correctness_business"]
        bound_skills = ["design-consistency-check"]

    gateway._plugin_loader.get = lambda tool_name: Plugin() if tool_name == "design_spec_alignment" else None  # type: ignore[method-assign]

    results = gateway.invoke_for_expert(
        expert,
        subject,
        RuntimeSettings(runtime_tool_allowlist=["design_spec_alignment"]),
        file_path="apps/api/order/service.ts",
        line_start=12,
        extra_tools=["design_spec_alignment"],
        active_skills=["other-skill"],
    )

    result = next(item for item in results if item["tool_name"] == "design_spec_alignment")
    assert result["success"] is False
    assert result["skipped"] is True
    assert result["skip_reason"] == "skill_not_bound"
