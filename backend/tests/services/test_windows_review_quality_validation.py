import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_windows_review_quality.py"


def _load_windows_validation_module():
    spec = importlib.util.spec_from_file_location("validate_windows_review_quality", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_windows_validation_dry_run_accepts_drive_space_and_chinese_paths() -> None:
    module = _load_windows_validation_module()

    report = module.build_windows_review_quality_report(
        workspace_path="C:\\业务 仓库\\中文项目\\mr-worktree",
        changed_files=["src\\main\\java\\CourseCreator.java"],
        prompt_text="workspace=C:/业务 仓库/中文项目/mr-worktree",
        model_name="minimax-2.5",
        dry_run=True,
    )

    assert report["passed"] is True
    assert report["normalized_workspace_path"] == "C:/业务 仓库/中文项目/mr-worktree"
    assert report["normalized_changed_files"] == ["src/main/java/CourseCreator.java"]
    assert report["prompt_profile"] == "rule-guided-compact"


def test_windows_validation_real_workspace_requires_graph_and_prompt_match(tmp_path: Path) -> None:
    module = _load_windows_validation_module()
    workspace = tmp_path / "repo with space"
    workspace.mkdir()

    report = module.build_windows_review_quality_report(
        workspace_path=str(workspace),
        changed_files=["src\\Missing.java"],
        prompt_text="workspace=/elsewhere",
        metadata={},
        model_name="doubao-seed-2.0-code",
        dry_run=False,
    )

    assert report["passed"] is False
    assert "git_repo_readable" in report["missing"]
    assert "tree_sitter_graph_ready" in report["missing"]
    assert "gitnexus_graph_ready" in report["missing"]
    assert "prompt_workspace_matches_mr_workspace" in report["missing"]
