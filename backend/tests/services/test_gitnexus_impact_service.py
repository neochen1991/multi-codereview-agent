from pathlib import Path
from unittest.mock import patch

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import write_json
from app.services.gitnexus_impact_service import GitNexusImpactService, GitNexusMcpImpactClient
from app.services.tool_gateway import ReviewToolGateway


class FakeGitNexusImpactClient:
    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols, runtime_env=None):
        return {
            "detect_changes": {
                "changed_symbols": ["StockRepository.findByStatus", "StockService.reserve"],
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
            "context_results": [
                {
                    "symbol": {"name": "StockRepository.findByStatus"},
                    "incoming": {"calls": [{"name": "StockService.reserve"}]},
                    "outgoing": {"calls": [{"name": "StockMapper.selectByStatus"}]},
                    "processes": [{"name": "库存扣减流程"}],
                }
            ],
            "impact_results": [
                {
                    "paths": [["StockController.create", "StockService.reserve", "StockRepository.findByStatus"]],
                }
            ],
        }


class CaptureGitNexusImpactClient:
    def __init__(self) -> None:
        self.changed_symbols = []

    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols, runtime_env=None):
        self.changed_symbols = list(changed_symbols)
        return {
            "detect_changes": {
                "affected_files": [],
                "affected_processes": ["inventory"],
            },
            "context_results": [],
            "impact_results": [],
        }


class FailingGitNexusImpactClient:
    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols, runtime_env=None):
        raise RuntimeError("mcp unavailable")


def test_gitnexus_mcp_impact_client_continues_when_detect_changes_returns_error():
    client = GitNexusMcpImpactClient(timeout_seconds=5)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        unified_diff="",
    )

    responses = [
        {2: {"result": {"content": [{"text": '[{"name":"repo"}]'}]}}},
        {2: {"error": {"code": -32000, "message": "detect failed", "data": {"reason": "repo not indexed"}}}},
        {2: {"result": {"content": [{"text": '{"symbol":{"name":"OrderService.create"},"incoming":{"calls":[{"name":"OrderController.create"}]}}'}]}}},
        {2: {"result": {"content": [{"text": '{"paths":[["OrderController.create","OrderService.create"]]}'}]}}},
    ]

    def fake_call_mcp(command, repo_path, requests, runtime_env=None):
        return responses.pop(0)

    with patch.object(client, "_call_mcp", side_effect=fake_call_mcp):
        payload = client.analyze_mr(
            repo_name="repo",
            repo_path="/tmp/repo",
            subject=subject,
            changed_symbols=[type("ChangedSymbol", (), {"symbol": "create", "container": ""})()],
            runtime_env=None,
        )

    assert payload["detect_changes"] == {}
    assert "GitNexus detect_changes 调用失败" in payload["detect_changes_error"]
    assert payload["context_results"]
    assert payload["impact_results"]


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

    with patch.object(service, "_registry_paths_for_repo_name", return_value=[]):
        report = service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert report.graph_status == "ready"
    assert report.graph_commit == "abc123"
    assert any(item.file_path.endswith("StockService.java") for item in report.impacted_files)
    assert any(item.scope == "库存扣减主链路回归" for item in report.recommended_test_scope)
    assert report.impact_paths[0].path[-1] == "StockRepository.findByStatus"
    assert any(node.label == "StockService.reserve" for node in report.impact_graph.nodes)
    assert any(node.role == "file" and node.file_path.endswith("StockService.java") for node in report.impact_graph.nodes)
    assert any(node.role == "test" and node.label == "库存扣减主链路回归" for node in report.impact_graph.nodes)
    assert any(node.role == "module" and node.label == "inventory" for node in report.impact_graph.nodes)
    assert any(edge.source == "StockService.reserve" and edge.target == "StockRepository.findByStatus" for edge in report.impact_graph.edges)


def test_gitnexus_impact_service_prefers_repo_local_graph_status_over_storage_root(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        repo_path / ".gitnexus" / "index_status.json",
        {
            "state": "ready",
            "repo_path": str(repo_path),
            "repo_name": "repo-local",
            "indexed_at": "2026-04-28T00:00:00+00:00",
            "commit": "repo-local-commit",
        },
    )
    write_json(
        storage_root / "gitnexus" / "index_status.json",
        {
            "state": "ready",
            "repo_path": str(tmp_path / "other-repo"),
            "repo_name": "wrong-repo",
            "indexed_at": "2026-04-27T00:00:00+00:00",
            "commit": "wrong-commit",
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

    assert report.graph_commit == "repo-local-commit"


def test_gitnexus_impact_service_reads_official_meta_json_when_local_status_missing(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo-meta"
    repo_path.mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        repo_path / ".gitnexus" / "meta.json",
        {
            "repoPath": str(repo_path),
            "lastCommit": "meta-commit",
            "indexedAt": "2026-04-28T00:00:00Z",
            "stats": {"files": 4, "nodes": 32},
        },
    )
    write_json(
        storage_root / "gitnexus" / "index_status.json",
        {
            "state": "ready",
            "repo_path": str(tmp_path / "other-repo"),
            "repo_name": "wrong-repo",
            "indexed_at": "2026-04-27T00:00:00+00:00",
            "commit": "wrong-commit",
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

    with patch("app.services.gitnexus_impact_service.shutil.which", return_value="/usr/local/bin/gitnexus"):
        report = service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert report.graph_commit == "meta-commit"
    assert report.graph_status == "ready"


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

    with patch.object(service, "_registry_paths_for_repo_name", return_value=[]):
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


def test_gitnexus_impact_service_rejects_duplicate_registry_repo_names(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        repo_path / ".gitnexus" / "meta.json",
        {
            "repoPath": str(repo_path),
            "lastCommit": "meta-commit",
            "indexedAt": "2026-04-28T00:00:00Z",
        },
    )
    duplicate_one = tmp_path / "duplicate-one"
    duplicate_two = tmp_path / "duplicate-two"
    duplicate_one.mkdir()
    duplicate_two.mkdir()

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

    with (
        patch("app.services.gitnexus_impact_service.shutil.which", return_value="/usr/local/bin/gitnexus"),
        patch.object(
            service,
            "_load_gitnexus_registry",
            return_value=[
                {"name": "repo", "path": str(duplicate_one)},
                {"name": "repo", "path": str(duplicate_two)},
            ],
        ),
    ):
        try:
            service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))
        except RuntimeError as error:
            assert "重复仓库名" in str(error)
        else:
            raise AssertionError("重复仓库名时应直接失败")


def test_gitnexus_impact_service_extracts_symbols_from_local_git_diff_when_subject_diff_missing(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        storage_root / "gitnexus" / "index_status.json",
        {"state": "ready", "repo_path": str(repo_path), "repo_name": "repo", "commit": "abc123"},
    )
    capture = CaptureGitNexusImpactClient()
    service = GitNexusImpactService(storage_root, mcp_client=capture)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        unified_diff="",
        metadata={"workspace_repo_path": str(repo_path)},
    )

    with (
        patch("app.services.gitnexus_impact_service.shutil.which", return_value="/usr/local/bin/gitnexus"),
        patch.object(
            service,
            "_load_local_diff_from_git",
            return_value=(
                "diff --git a/src/main/java/com/example/OrderController.java "
                "b/src/main/java/com/example/OrderController.java\n"
                "@@ -10,0 +10,4 @@\n"
                " public class OrderController {\n"
                "+  public OrderDTO createOrder() {\n"
                "+    return service.create();\n"
                "+  }\n"
                " }\n"
            ),
        ),
    ):
        with patch.object(service, "_registry_paths_for_repo_name", return_value=[]):
            report = service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert report.graph_status == "ready"
    assert capture.changed_symbols
    assert capture.changed_symbols[0].symbol == "createOrder"
    assert capture.changed_symbols[0].container == "OrderController"


def test_gitnexus_impact_service_prefers_platform_diff_without_local_git_enrichment(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    service = GitNexusImpactService(storage_root)
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
            "@@ -10,0 +10,4 @@\n"
            " public class OrderController {\n"
            "+  public OrderDTO createOrder() {\n"
            "+    return service.create();\n"
            "+  }\n"
            " }\n"
        ),
        metadata={"workspace_repo_path": str(repo_path)},
    )

    with (
        patch.object(service, "_enrich_changed_symbols_from_source", side_effect=AssertionError("should not enrich from local git")),
        patch.object(service, "_load_local_diff_from_git", side_effect=AssertionError("should not load local git diff")),
        patch.object(service, "_scan_changed_file_symbols", side_effect=AssertionError("should not scan local git source")),
    ):
        symbols = service._build_changed_symbols(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert any(item.symbol == "createOrder" and item.container == "OrderController" for item in symbols)


def test_gitnexus_local_git_diff_resolves_available_refs_before_running(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/missing",
        target_ref="main",
        commits=["abc123"],
        changed_files=["src/main/java/com/example/OrderController.java"],
        unified_diff="",
    )

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None):
        if command[:3] == ["git", "rev-parse", "--verify"]:
            ref = command[3].replace("^{commit}", "")
            if ref in {"abc123", "origin/main"}:
                return type("Completed", (), {"returncode": 0, "stdout": f"{ref}\n", "stderr": ""})()
            return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "unknown revision"})()
        if command[:3] == ["git", "diff", "--unified=3"]:
            assert command[3] == "origin/main"
            assert command[4] == "abc123"
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": "diff --git a/foo b/foo\n@@ -1,0 +1,1 @@\n+demo\n",
                    "stderr": "",
                },
            )()
        raise AssertionError(f"unexpected command: {command}")

    with patch("app.services.gitnexus_impact_service.subprocess.run", side_effect=fake_run):
        diff = service._load_local_diff_from_git(str(repo_path), subject)

    assert "+demo" in diff


def test_gitnexus_scan_changed_file_symbols_uses_resolved_git_refs(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/missing",
        target_ref="main",
        commits=["abc123"],
        changed_files=["src/main/java/com/example/OrderController.java"],
        unified_diff="",
    )

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None):
        if command[:3] == ["git", "rev-parse", "--verify"]:
            ref = command[3].replace("^{commit}", "")
            if ref in {"abc123", "origin/main"}:
                return type("Completed", (), {"returncode": 0, "stdout": f"{ref}\n", "stderr": ""})()
            return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "unknown revision"})()
        if command[:2] == ["git", "show"]:
            assert command[2] == "abc123:src/main/java/com/example/OrderController.java"
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": "public class OrderController {\n  public OrderDTO createOrder() {\n    return service.create();\n  }\n}\n",
                    "stderr": "",
                },
            )()
        raise AssertionError(f"unexpected command: {command}")

    with patch("app.services.gitnexus_impact_service.subprocess.run", side_effect=fake_run):
        symbols = service._scan_changed_file_symbols(str(repo_path), subject)

    assert any(item.symbol == "createOrder" and item.container == "OrderController" for item in symbols)


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
