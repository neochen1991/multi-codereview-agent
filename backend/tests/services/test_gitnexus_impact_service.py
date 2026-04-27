from pathlib import Path
from unittest.mock import patch

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import write_json
from app.services.gitnexus_impact_service import GitNexusImpactService
from app.services.tool_gateway import ReviewToolGateway


class FakeGitNexusImpactClient:
    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols):
        return {
            "detect_changes": {
                "affected_files": [
                    {
                        "path": "inventory/src/main/java/com/example/StockService.java",
                        "relationship": "caller",
                        "reason": "StockRepository 被 StockService 调用。",
                        "riskLevel": "high",
                    }
                ],
                "affected_processes": ["inventory"],
                "testRecommendations": [
                    {
                        "scope": "库存扣减主链路回归",
                        "reason": "Repository 查询影响库存服务调用链。",
                        "paths": ["inventory/src/test/java/com/example/StockServiceTest.java"],
                        "priority": "high",
                    }
                ],
            },
            "impact_results": [
                {
                    "paths": [["StockController.create", "StockService.reserve", "StockRepository.findByStatus"]],
                }
            ],
        }


class FailingGitNexusImpactClient:
    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols):
        raise RuntimeError("mcp unavailable")


def test_gitnexus_impact_service_builds_fallback_report(storage_root: Path):
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/batch",
        target_ref="main",
        changed_files=["inventory/src/main/java/com/example/StockRepository.java"],
        unified_diff=(
            "diff --git a/inventory/src/main/java/com/example/StockRepository.java "
            "b/inventory/src/main/java/com/example/StockRepository.java\n"
            "@@ -20,2 +20,5 @@\n"
            "+public List<Stock> findByStatus(String status) {\n"
            "+    return jdbcTemplate.query(sql, mapper);\n"
            "+}\n"
        ),
    )

    try:
        service.analyze(subject, RuntimeSettings())
    except RuntimeError as error:
        assert "本地代码仓路径" in str(error) or "图谱未就绪" in str(error)
    else:
        raise AssertionError("GitNexus 不可用时不应再生成降级报告")


def test_gitnexus_impact_service_uses_ready_graph_before_fallback(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        storage_root / "gitnexus" / "index_status.json",
        {
            "state": "ready",
            "repo_path": str(repo_path),
            "repo_name": "repo",
            "indexed_at": "2026-04-27T00:00:00+00:00",
            "commit": "abc123",
        },
    )
    service = GitNexusImpactService(storage_root, mcp_client=FakeGitNexusImpactClient())
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/batch",
        target_ref="main",
        changed_files=["inventory/src/main/java/com/example/StockRepository.java"],
        unified_diff=(
            "diff --git a/inventory/src/main/java/com/example/StockRepository.java "
            "b/inventory/src/main/java/com/example/StockRepository.java\n"
            "@@ -20,2 +20,5 @@\n"
            "+public List<Stock> findByStatus(String status) {\n"
            "+    return jdbcTemplate.query(sql, mapper);\n"
            "+}\n"
        ),
        metadata={"workspace_repo_path": str(repo_path)},
    )

    report = service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert report.graph_status == "ready"
    assert report.graph_commit == "abc123"
    assert any(item.file_path.endswith("StockService.java") for item in report.impacted_files)
    assert any(item.scope == "库存扣减主链路回归" for item in report.recommended_test_scope)
    assert report.impact_paths[0].path[-1] == "StockRepository.findByStatus"


def test_gitnexus_impact_service_marks_fallback_when_ready_graph_call_fails(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        storage_root / "gitnexus" / "index_status.json",
        {"state": "ready", "repo_path": str(repo_path), "repo_name": "repo", "commit": "abc123"},
    )
    service = GitNexusImpactService(storage_root, mcp_client=FailingGitNexusImpactClient())
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderController.java "
            "b/src/main/java/com/example/OrderController.java\n"
            "@@ -1,1 +1,3 @@\n"
            "+public OrderDTO create() {\n"
            "+}\n"
        ),
        metadata={"workspace_repo_path": str(repo_path)},
    )

    try:
        service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))
    except RuntimeError as error:
        assert "按官方 MCP 流程调用失败" in str(error)
    else:
        raise AssertionError("GitNexus MCP 调用失败时不应再生成降级报告")


def test_gitnexus_impact_service_requires_preinstalled_gitnexus(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        storage_root / "gitnexus" / "index_status.json",
        {"state": "ready", "repo_path": str(repo_path), "repo_name": "repo", "commit": "abc123"},
    )
    service = GitNexusImpactService(storage_root, mcp_client=FakeGitNexusImpactClient())
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderController.java "
            "b/src/main/java/com/example/OrderController.java\n"
            "@@ -1,1 +1,3 @@\n"
            "+public OrderDTO create() {\n"
            "+}\n"
        ),
        metadata={"workspace_repo_path": str(repo_path)},
    )

    with patch("app.services.gitnexus_impact_service.shutil.which", return_value=None):
        try:
            service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))
        except RuntimeError as error:
            assert "未预装 GitNexus" in str(error)
        else:
            raise AssertionError("未安装 GitNexus 时应直接失败")


def test_tool_gateway_invokes_gitnexus_impact_analysis(storage_root: Path):
    gateway = ReviewToolGateway(storage_root)
    expert = ExpertProfile(
        expert_id="change_impact_analysis",
        name="change-impact-analysis",
        name_zh="关联性影响分析专家",
        role="影响分析",
        runtime_tool_bindings=["gitnexus_impact_analysis"],
        max_tool_calls=1,
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderController.java "
            "b/src/main/java/com/example/OrderController.java\n"
            "@@ -1,1 +1,3 @@\n"
            "+public OrderDTO create() {\n"
            "+}\n"
        ),
    )

    try:
        gateway.invoke_for_expert(
            expert,
            subject,
            RuntimeSettings(),
            file_path="src/main/java/com/example/OrderController.java",
            line_start=1,
        )
    except RuntimeError as error:
        assert "本地代码仓路径" in str(error)
    else:
        raise AssertionError("缺少 GitNexus 前置条件时，工具调用应明确失败")
