from pathlib import Path

from app.services.expert_registry import ExpertRegistry


def test_expert_registry_loads_builtin_experts(storage_root: Path):
    registry = ExpertRegistry(storage_root / "experts")
    experts = registry.list_enabled()
    assert len(experts) >= 6
    assert any(expert.name_zh == "安全与合规专家" for expert in experts)
    architecture = next(expert for expert in experts if expert.expert_id == "architecture_design")
    security = next(expert for expert in experts if expert.expert_id == "security_compliance")
    assert architecture.activation_hints
    assert architecture.required_checks
    assert architecture.tool_bindings == ["local_diff"]
    assert len(architecture.system_prompt) > 80
    assert security.preferred_artifacts
    assert "auth" in security.activation_hints or "security" in security.activation_hints
