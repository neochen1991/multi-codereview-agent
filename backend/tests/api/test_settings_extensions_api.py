from pathlib import Path

import app.services.review_service as review_service_module
from app.services.extension_editor_service import ExtensionEditorService


def test_settings_extensions_skill_can_be_created_and_listed(client, tmp_path: Path):
    review_service_module.review_service.extension_editor_service = ExtensionEditorService(tmp_path)

    response = client.put(
        "/api/settings/extensions/skills/design_consistency_check",
        json={
            "skill_id": "design_consistency_check",
            "name": "设计一致性检查",
            "description": "校验实现与详细设计文档是否一致",
            "bound_experts": ["correctness_business"],
            "required_tools": ["design_spec_alignment"],
            "activation_hints": ["design", "api", "schema"],
            "allowed_modes": ["standard", "light"],
            "prompt_body": "# Skill\n\n请调用设计一致性工具并输出差异。",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["skill_id"] == "design_consistency_check"
    assert payload["name"] == "设计一致性检查"
    assert "请调用设计一致性工具" in payload["prompt_body"]

    listed = client.get("/api/settings/extensions/skills")
    assert listed.status_code == 200
    rows = listed.json()
    item = next(value for value in rows if value["skill_id"] == "design_consistency_check")
    assert item["bound_experts"] == ["correctness_business"]


def test_settings_extensions_tool_can_be_created_and_listed(client, tmp_path: Path):
    review_service_module.review_service.extension_editor_service = ExtensionEditorService(tmp_path)

    response = client.put(
        "/api/settings/extensions/tools/design_spec_alignment",
        json={
            "tool_id": "design_spec_alignment",
            "name": "设计文档一致性工具",
            "description": "提取详细设计并比对代码实现",
            "runtime": "python",
            "entry": "run.py",
            "timeout_seconds": 180,
            "allowed_experts": ["correctness_business"],
            "bound_skills": ["design_consistency_check"],
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "run_script": "def run(payload):\n    return {\"ok\": True}\n",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_id"] == "design_spec_alignment"
    assert "def run" in payload["run_script"]

    listed = client.get("/api/settings/extensions/tools")
    assert listed.status_code == 200
    rows = listed.json()
    item = next(value for value in rows if value["tool_id"] == "design_spec_alignment")
    assert item["allowed_experts"] == ["correctness_business"]
    assert item["bound_skills"] == ["design_consistency_check"]
