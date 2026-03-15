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
