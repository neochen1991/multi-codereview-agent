from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.tool_gateway import ReviewToolGateway


def test_skill_gateway_adds_repo_context_search_for_all_experts(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    target = repo_root / "src" / "service.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const foo = () => repo.search()\n", encoding="utf-8")

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
        runtime_tool_bindings=["knowledge_search"],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/service.ts"],
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        code_repo_local_path=str(repo_root),
        code_repo_default_branch="main",
        runtime_tool_allowlist=["knowledge_search", "repo_context_search"],
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="src/service.ts",
        line_start=1,
    )

    tool_names = {item["tool_name"] for item in results}
    assert "repo_context_search" in tool_names


def test_skill_gateway_repo_context_search_returns_related_contexts(tmp_path: Path):
    storage_root = tmp_path / "storage"
    repo_root = tmp_path / "repo"
    primary = repo_root / "src" / "service.ts"
    related = repo_root / "src" / "transform.ts"
    primary.parent.mkdir(parents=True)
    primary.write_text("export const foo = () => transform()\n", encoding="utf-8")
    related.write_text("export const transform = () => 'ok'\n", encoding="utf-8")

    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/service.ts", "src/transform.ts"],
    )
    runtime = RuntimeSettings(
        code_repo_clone_url="https://github.com/example/repo.git",
        code_repo_local_path=str(repo_root),
        code_repo_default_branch="main",
        runtime_tool_allowlist=["repo_context_search"],
    )

    results = gateway.invoke_for_expert(
        expert,
        subject,
        runtime,
        file_path="src/service.ts",
        line_start=1,
        related_files=["src/transform.ts"],
    )

    repo_result = next(item for item in results if item["tool_name"] == "repo_context_search")
    assert "src/service.ts" in repo_result["context_files"]
    assert "src/transform.ts" in repo_result["context_files"]
    assert repo_result["related_contexts"]
    assert "symbol_contexts" in repo_result
