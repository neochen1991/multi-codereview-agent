from pathlib import Path

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.review_environment_preflight_service import run_review_environment_preflight


def test_preflight_normalizes_windows_paths_and_detects_diff_mapping(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    target = workspace / "src" / "CourseCreator.java"
    target.parent.mkdir(parents=True)
    target.write_text("class CourseCreator {}", encoding="utf-8")

    result = run_review_environment_preflight(
        ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature",
            target_ref="main",
            changed_files=["src\\CourseCreator.java"],
            unified_diff="diff --git a/src/CourseCreator.java b/src/CourseCreator.java\n",
            metadata={"workspace_repo_path": str(workspace)},
        ),
        RuntimeSettings(code_repo_local_path=str(workspace)),
    )

    assert result["status"] == "passed"
    assert result["normalized_changed_files"] == ["src/CourseCreator.java"]
    assert result["path_resolution_failures"] == []


def test_preflight_reports_missing_workspace_and_files() -> None:
    result = run_review_environment_preflight(
        ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature",
            target_ref="main",
            changed_files=["src\\Missing.java"],
            unified_diff="diff --git a/src/Missing.java b/src/Missing.java\n",
        ),
        RuntimeSettings(code_repo_local_path=""),
    )

    assert result["status"] == "warning"
    assert "workspace_missing" in result["degraded_context_reasons"]
    assert result["normalized_changed_files"] == ["src/Missing.java"]


def test_preflight_reports_graph_degradation(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    target = workspace / "src" / "CourseCreator.java"
    target.parent.mkdir(parents=True)
    target.write_text("class CourseCreator {}", encoding="utf-8")

    result = run_review_environment_preflight(
        ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature",
            target_ref="main",
            changed_files=["src/CourseCreator.java"],
            metadata={
                "workspace_repo_path": str(workspace),
                "code_graph_db_path": str(workspace / ".code-review-graph" / "graph.db"),
                "tree_sitter_graph_result": {"status": "failed", "error": "parser unavailable"},
                "gitnexus_graph_result": {"state": "degraded", "error": "index stale"},
            },
        ),
        RuntimeSettings(code_repo_local_path=str(workspace)),
    )

    assert result["status"] == "warning"
    assert "code_graph_db_missing" in result["degraded_context_reasons"]
    assert "tree_sitter_graph_degraded" in result["degraded_context_reasons"]
    assert "gitnexus_graph_degraded" in result["degraded_context_reasons"]
