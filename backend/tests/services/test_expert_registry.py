from pathlib import Path

from app.repositories.file_expert_repository import FileExpertRepository
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
    assert "架构与设计审视规范" in architecture.review_spec
    assert security.preferred_artifacts
    assert "auth" in security.activation_hints or "security" in security.activation_hints


def test_file_expert_repository_preserves_builtin_review_spec_when_user_override_has_no_spec(storage_root: Path):
    repository = FileExpertRepository(storage_root / "experts")
    builtin = next(expert for expert in repository.list() if expert.expert_id == "architecture_design")
    repository.save(
        builtin.model_copy(
            update={
                "system_prompt": "custom prompt",
                "review_spec": "",
            }
        )
    )

    loaded = next(expert for expert in repository.list() if expert.expert_id == "architecture_design")

    assert loaded.system_prompt == "custom prompt"
    assert "架构与设计审视规范" in loaded.review_spec


def test_file_expert_repository_merges_extension_bound_skills(storage_root: Path):
    repository = FileExpertRepository(storage_root / "experts")

    correctness = next(expert for expert in repository.list() if expert.expert_id == "correctness_business")

    assert "design-consistency-check" in correctness.skill_bindings
