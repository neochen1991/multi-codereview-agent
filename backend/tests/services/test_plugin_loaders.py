from pathlib import Path

from app.services.review_skill_registry import ReviewSkillRegistry
from app.services.tool_plugin_loader import ToolPluginLoader


def test_review_skill_registry_loads_design_consistency_skill():
    root = Path("extensions/skills")
    registry = ReviewSkillRegistry(root)

    skill = registry.get("design-consistency-check")

    assert skill is not None
    assert skill.skill_id == "design-consistency-check"
    assert "correctness_business" in skill.bound_experts
    assert "correctness_business" in skill.applicable_experts
    assert "design_spec_alignment" in skill.required_tools
    assert "SKILL.md" not in skill.prompt_body


def test_tool_plugin_loader_loads_design_spec_alignment():
    root = Path("extensions/tools")
    loader = ToolPluginLoader(root)

    plugin = loader.get("design_spec_alignment")

    assert plugin is not None
    assert plugin.runtime == "python"
    assert plugin.entry == "run.py"
    assert "correctness_business" in plugin.allowed_experts
