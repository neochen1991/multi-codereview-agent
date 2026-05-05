import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.report import ImpactSymbol
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import CodeRepositorySettings, RuntimeSettings
from app.repositories.fs import write_json
from app.services.gitnexus_impact_service import GitNexusImpactService, GitNexusMcpImpactClient
from app.services.review_service import ReviewService
from app.services.tool_gateway import ReviewToolGateway


class FakeGitNexusImpactClient:
    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols, runtime_env=None, **kwargs):
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
        self.repo_name = ""
        self.runtime_env = None
        self.cli_available_repos = []

    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols, runtime_env=None, **kwargs):
        self.repo_name = repo_name
        self.runtime_env = runtime_env
        self.cli_available_repos = list(kwargs.get("cli_available_repos") or [])
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
    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols, runtime_env=None, **kwargs):
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

    def fake_call_mcp(command, repo_path, requests, runtime_env=None):
        responses = {}
        for request in requests:
            params = request.get("params") or {}
            tool = params.get("name")
            request_id = request.get("id")
            if tool == "list_repos":
                responses[request_id] = {"result": {"content": [{"text": '[{"name":"repo"}]'}]}}
            elif tool == "detect_changes":
                responses[request_id] = {"error": {"code": -32000, "message": "detect failed", "data": {"reason": "repo not indexed"}}}
            elif tool == "context":
                responses[request_id] = {
                    "result": {"content": [{"text": '{"symbol":{"name":"OrderService.create"},"incoming":{"calls":[{"name":"OrderController.create"}]}}'}]}
                }
            elif tool == "impact":
                responses[request_id] = {
                    "result": {"content": [{"text": '{"paths":[["OrderController.create","OrderService.create"]]}'}]}
                }
        return responses

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
    assert "create" in payload["successful_context_targets"]
    assert "create" in payload["successful_impact_targets"]
    assert payload["dynamic_targets"] == ["OrderController.create"]


def test_gitnexus_mcp_impact_client_batches_one_analysis_into_two_mcp_calls(caplog):
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
    call_batches: list[list[dict[str, object]]] = []

    def fake_call_mcp(command, repo_path, requests, runtime_env=None):
        call_batches.append(requests)
        responses = {}
        for request in requests:
            params = request.get("params") or {}
            tool = params.get("name")
            args = params.get("arguments") or {}
            request_id = request.get("id")
            if tool == "list_repos":
                responses[request_id] = {"result": {"content": [{"text": '[{"name":"repo"}]'}]}}
            elif tool == "detect_changes":
                responses[request_id] = {"result": {"content": [{"text": '{"changed_symbols":[]}'}]}}
            elif tool == "context":
                responses[request_id] = {
                    "result": {"content": [{"text": '{"symbol":{"name":"%s"}}' % args.get("name")}]}
                }
            elif tool == "impact":
                responses[request_id] = {
                    "result": {"content": [{"text": '{"paths":[["Controller","%s"]]}' % args.get("target")}]}
                }
        return responses

    with caplog.at_level(logging.INFO, logger="app.services.gitnexus_impact_service"):
        with patch.object(client, "_call_mcp", side_effect=fake_call_mcp):
            payload = client.analyze_mr(
                repo_name="repo",
                repo_path="/tmp/repo",
                subject=subject,
                changed_symbols=[
                    type("ChangedSymbol", (), {"symbol": "create", "container": "OrderService"})(),
                    type("ChangedSymbol", (), {"symbol": "save", "container": "OrderRepository"})(),
                ],
                runtime_env=None,
            )

    assert len(call_batches) == 3
    tool_names = [
        (request.get("params") or {}).get("name")
        for batch in call_batches
        for request in batch
        if request.get("method") == "tools/call"
    ]
    assert tool_names[:2] == ["list_repos", "detect_changes"]
    assert tool_names.count("context") == 6
    assert tool_names.count("impact") == 6
    assert len(payload["context_results"]) == 6
    assert len(payload["impact_results"]) == 6
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "gitnexus mcp tool request tool=context" in log_text
    assert '"name": "OrderService.create"' in log_text
    assert "gitnexus mcp tool response tool=impact" in log_text
    assert '"paths": [["Controller", "OrderService.create"]]' in log_text


def test_gitnexus_mcp_impact_client_continues_when_list_repos_misses_registry_repo(caplog):
    client = GitNexusMcpImpactClient(timeout_seconds=5)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-b",
        project_id="proj",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        unified_diff="",
    )
    call_batches: list[list[dict[str, object]]] = []

    def fake_call_mcp(command, repo_path, requests, runtime_env=None):
        del command, repo_path, runtime_env
        call_batches.append(requests)
        responses = {}
        for request in requests:
            params = request.get("params") or {}
            if params.get("name") == "list_repos":
                responses[request.get("id")] = {"result": {"content": [{"text": '[{"name":"repo-a"}]'}]}}
        return responses

    with caplog.at_level(logging.WARNING, logger="app.services.gitnexus_impact_service"):
        with patch.object(client, "_call_mcp", side_effect=fake_call_mcp):
            payload = client.analyze_mr(
                repo_name="repo-b",
                repo_path="/tmp/repo-b",
                subject=subject,
                changed_symbols=[type("ChangedSymbol", (), {"symbol": "create", "container": "OrderService"})()],
                runtime_env=None,
                cli_available_repos=["repo-a", "repo-b"],
            )

    tool_names = [
        (request.get("params") or {}).get("name")
        for batch in call_batches
        for request in batch
        if request.get("method") == "tools/call"
    ]
    assert tool_names[:2] == ["list_repos", "detect_changes"]
    assert payload["repo"] == "repo-b"
    assert payload["available_repos"] == ["repo-a"]
    assert payload["cli_available_repos"] == ["repo-a", "repo-b"]
    assert payload["cli_list_has_repo"] is True
    assert payload["list_repos_missing_repo"] is True
    assert "list_repos did not include target repo" in caplog.text


def test_gitnexus_mcp_impact_client_respects_runtime_query_limits():
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
    call_batches: list[list[dict[str, object]]] = []

    def fake_call_mcp(command, repo_path, requests, runtime_env=None):
        call_batches.append(requests)
        responses = {}
        for request in requests:
            params = request.get("params") or {}
            tool = params.get("name")
            args = params.get("arguments") or {}
            request_id = request.get("id")
            if tool == "list_repos":
                responses[request_id] = {"result": {"content": [{"text": '[{"name":"repo"}]'}]}}
            elif tool == "detect_changes":
                responses[request_id] = {"result": {"content": [{"text": '{"changed_symbols":["OrderController.create","OrderService.create","OrderRepository.save"]}'}]}}
            elif tool == "context":
                responses[request_id] = {"result": {"content": [{"text": '{"symbol":{"name":"%s"}}' % args.get("name")}]}}
            elif tool == "impact":
                responses[request_id] = {"result": {"content": [{"text": '{"paths":[["Entry","%s"]]}' % args.get("target")}]}}
        return responses

    with patch.object(client, "_call_mcp", side_effect=fake_call_mcp):
        payload = client.analyze_mr(
            repo_name="repo",
            repo_path="/tmp/repo",
            subject=subject,
            changed_symbols=[],
            runtime_env=None,
            max_targets=2,
            max_context_queries=1,
            max_impact_queries=2,
            max_dynamic_targets=0,
        )

    tool_names = [
        (request.get("params") or {}).get("name")
        for batch in call_batches
        for request in batch
        if request.get("method") == "tools/call"
    ]
    assert tool_names.count("context") == 1
    assert tool_names.count("impact") == 2
    assert len(payload["queried_targets"]) == 2
    assert payload["dynamic_targets"] == []


def test_gitnexus_mcp_impact_client_discovers_dynamic_targets_from_context():
    client = GitNexusMcpImpactClient(timeout_seconds=5)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff="",
    )
    call_batches: list[list[dict[str, object]]] = []

    def fake_call_mcp(command, repo_path, requests, runtime_env=None):
        call_batches.append(requests)
        responses = {}
        for request in requests:
            params = request.get("params") or {}
            tool = params.get("name")
            args = params.get("arguments") or {}
            request_id = request.get("id")
            if tool == "list_repos":
                responses[request_id] = {"result": {"content": [{"text": '[{"name":"repo"}]'}]}}
            elif tool == "detect_changes":
                responses[request_id] = {"result": {"content": [{"text": '{"changed_symbols":[]}'}]}}
            elif tool == "context":
                name = str(args.get("name") or "")
                if name == "OrderService.create":
                    responses[request_id] = {
                        "result": {
                            "content": [
                                {
                                    "text": (
                                        '{"symbol":{"name":"OrderService.create"},'
                                        '"incoming":{"calls":[{"name":"OrderController.create"}]},'
                                        '"outgoing":{"calls":[{"name":"PaymentClient.reserve"}]}}'
                                    )
                                }
                            ]
                        }
                    }
                else:
                    responses[request_id] = {"result": {"content": [{"text": '{"symbol":{"name":"%s"}}' % name}]}}
            elif tool == "impact":
                responses[request_id] = {
                    "result": {"content": [{"text": '{"paths":[["Entry","%s"]]}' % args.get("target")}]}
                }
        return responses

    with patch.object(client, "_call_mcp", side_effect=fake_call_mcp):
        payload = client.analyze_mr(
            repo_name="repo",
            repo_path="/tmp/repo",
            subject=subject,
            changed_symbols=[type("ChangedSymbol", (), {"symbol": "create", "container": "OrderService"})()],
            runtime_env=None,
        )

    assert len(call_batches) == 4
    assert "OrderController.create" in payload["queried_targets"]
    assert "PaymentClient.reserve" in payload["queried_targets"]
    assert payload["dynamic_targets"] == ["OrderController.create", "PaymentClient.reserve"]
    assert "OrderController.create" in payload["successful_impact_targets"]
    assert "PaymentClient.reserve" in payload["successful_impact_targets"]


def test_gitnexus_mcp_impact_client_filters_invalid_symbol_targets():
    client = GitNexusMcpImpactClient(timeout_seconds=5)
    targets, skipped_invalid = client._build_targets(
        [
            type("ChangedSymbol", (), {"symbol": "synchronized", "container": ""})(),
            type("ChangedSymbol", (), {"symbol": "createOrder", "container": "OrderService"})(),
        ],
        {"changed_symbols": ["private", "OrderController.createOrder"]},
    )

    assert "synchronized" not in targets
    assert "private" not in targets
    assert "OrderService.createOrder" in targets
    assert "OrderController.createOrder" in targets
    assert skipped_invalid == ["synchronized", "private"]


def test_gitnexus_mcp_impact_client_prioritizes_high_value_targets():
    client = GitNexusMcpImpactClient(timeout_seconds=5)
    targets, skipped_invalid = client._build_targets(
        [
            type(
                "ChangedSymbol",
                (),
                {
                    "symbol": "OrderDto",
                    "container": "",
                    "file_path": "src/main/java/com/example/dto/OrderDto.java",
                    "kind": "class",
                    "line_start": 4,
                },
            )(),
            type(
                "ChangedSymbol",
                (),
                {
                    "symbol": "createOrder",
                    "container": "OrderController",
                    "file_path": "src/main/java/com/example/api/OrderController.java",
                    "kind": "function",
                    "line_start": 24,
                },
            )(),
            type(
                "ChangedSymbol",
                (),
                {
                    "symbol": "save",
                    "container": "OrderRepository",
                    "file_path": "src/main/java/com/example/repository/OrderRepository.java",
                    "kind": "function",
                    "line_start": 30,
                },
            )(),
        ],
        {"changed_symbols": ["OrderService.createOrder", "private"]},
    )

    assert skipped_invalid == ["private"]
    assert targets[0] == "OrderController.createOrder"
    assert "OrderRepository.save" in targets[:3]
    assert "OrderDto" in targets


def test_gitnexus_mcp_impact_client_uses_feedback_profiles_in_target_priority():
    client = GitNexusMcpImpactClient(timeout_seconds=5)
    targets, skipped_invalid = client._build_targets(
        [
            type("ChangedSymbol", (), {"symbol": "create", "container": "OrderController", "file_path": "OrderController.java", "kind": "function", "line_start": 12})(),
            type("ChangedSymbol", (), {"symbol": "save", "container": "OrderRepository", "file_path": "OrderRepository.java", "kind": "function", "line_start": 20})(),
        ],
        {},
        impact_feedback_profiles={
            "targets": {
                "impact_path:OrderController.create -> OrderRepository.save": {
                    "target_type": "impact_path",
                    "target_key": "OrderController.create -> OrderRepository.save",
                    "sample_count": 4,
                    "false_positive_rate": 0.75,
                    "recommended_action": "require_manual_verification",
                }
            }
        },
    )

    assert skipped_invalid == []
    assert targets.index("OrderRepository.save") > targets.index("OrderController.create")


def test_gitnexus_mcp_impact_client_skips_missing_symbol_context_and_impact():
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

    def fake_call_mcp(command, repo_path, requests, runtime_env=None):
        responses = {}
        for request in requests:
            params = request.get("params") or {}
            tool = params.get("name")
            args = params.get("arguments") or {}
            request_id = request.get("id")
            if tool == "list_repos":
                responses[request_id] = {"result": {"content": [{"text": '[{"name":"repo"}]'}]}}
            elif tool == "detect_changes":
                responses[request_id] = {"result": {"content": [{"text": '{"changed_symbols":["MissingSymbol","OrderService.createOrder"]}'}]}}
            elif tool == "context":
                if args.get("name") == "MissingSymbol":
                    responses[request_id] = {"error": {"code": -32001, "message": "Symbol MissingSymbol not found"}}
                else:
                    responses[request_id] = {"result": {"content": [{"text": '{"symbol":{"name":"OrderService.createOrder"}}'}]}}
            elif tool == "impact":
                if args.get("target") == "MissingSymbol":
                    responses[request_id] = {"error": {"code": -32001, "message": "Symbol MissingSymbol not found"}}
                else:
                    responses[request_id] = {"result": {"content": [{"text": '{"paths":[["OrderController.createOrder","OrderService.createOrder"]]}'}]}}
        return responses

    with patch.object(client, "_call_mcp", side_effect=fake_call_mcp):
        payload = client.analyze_mr(
            repo_name="repo",
            repo_path="/tmp/repo",
            subject=subject,
            changed_symbols=[],
            runtime_env=None,
        )

    assert len(payload["context_results"]) == 1
    assert len(payload["impact_results"]) == 1
    assert payload["queried_targets"][0] == "OrderService.createOrder"
    assert set(payload["queried_targets"]) == {"MissingSymbol", "OrderService.createOrder"}
    assert payload["skipped_missing_context_targets"] == ["MissingSymbol"]
    assert payload["skipped_missing_impact_targets"] == ["MissingSymbol"]
    assert payload["successful_context_targets"] == ["OrderService.createOrder"]
    assert payload["successful_impact_targets"] == ["OrderService.createOrder"]


def test_gitnexus_mcp_impact_client_skips_missing_target_impact_error():
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

    def fake_call_mcp(command, repo_path, requests, runtime_env=None):
        responses = {}
        for request in requests:
            params = request.get("params") or {}
            tool = params.get("name")
            args = params.get("arguments") or {}
            request_id = request.get("id")
            if tool == "list_repos":
                responses[request_id] = {"result": {"content": [{"text": '[{"name":"repo"}]'}]}}
            elif tool == "detect_changes":
                responses[request_id] = {
                    "result": {
                        "content": [
                            {"text": '{"changed_symbols":["OrderApplicationService.buildOrder","OrderController.notifyAudit"]}'}
                        ]
                    }
                }
            elif tool == "context":
                target = args.get("name")
                responses[request_id] = {"result": {"content": [{"text": f'{{"symbol":{{"name":"{target}"}}}}'}]}}
            elif tool == "impact":
                target = args.get("target")
                if target == "OrderApplicationService.buildOrder":
                    responses[request_id] = {"error": {"code": -32001, "message": "Target 'OrderApplicationService.buildOrder' not found"}}
                else:
                    responses[request_id] = {"result": {"content": [{"text": '{"paths":[["OrderController.createOrder","OrderController.notifyAudit"]]}'}]}}
        return responses

    with patch.object(client, "_call_mcp", side_effect=fake_call_mcp):
        payload = client.analyze_mr(
            repo_name="repo",
            repo_path="/tmp/repo",
            subject=subject,
            changed_symbols=[],
            runtime_env=None,
        )

    assert len(payload["context_results"]) == 2
    assert len(payload["impact_results"]) == 1
    assert "OrderApplicationService.buildOrder" in payload["skipped_missing_impact_targets"]
    assert payload["successful_impact_targets"] == ["OrderController.notifyAudit"]


def test_gitnexus_mcp_impact_client_caches_duplicate_tool_target_queries():
    client = GitNexusMcpImpactClient(timeout_seconds=5)
    calls: list[tuple[str, str]] = []

    def fake_call_tool(command, repo_path, tool_name, arguments, runtime_env=None):
        target = str(arguments.get("name") or arguments.get("target") or "")
        calls.append((tool_name, target))
        return {"tool": tool_name, "target": target}

    with patch.object(client, "_call_tool", side_effect=fake_call_tool):
        cache: dict[tuple[str, str], dict[str, object]] = {}
        context_results, _, context_success = client._query_contexts(
            ["gitnexus", "mcp"],
            "repo",
            "/tmp/repo",
            ["OrderService.create", "OrderService.create"],
            query_cache=cache,
        )
        impact_results, _, impact_success = client._query_impacts(
            ["gitnexus", "mcp"],
            "repo",
            "/tmp/repo",
            ["OrderService.create", "OrderService.create"],
            query_cache=cache,
        )

    assert calls == [("context", "OrderService.create"), ("impact", "OrderService.create")]
    assert len(context_results) == 2
    assert len(impact_results) == 2
    assert context_success == ["OrderService.create", "OrderService.create"]
    assert impact_success == ["OrderService.create", "OrderService.create"]


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

    report = service.analyze(subject, RuntimeSettings())

    assert report.graph_status == "degraded"
    assert any("GitNexus 图谱不可用" in item for item in report.limitations)


def test_gitnexus_impact_service_surfaces_skipped_symbols_in_report(storage_root: Path, tmp_path: Path):
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
        report = service._normalize_gitnexus_payload(
            subject,
            RuntimeSettings(code_repo_local_path=str(repo_path)),
            {"indexed_at": "2026-04-27T00:00:00+00:00", "commit": "abc123"},
            {
                "detect_changes": {},
                "context_results": [],
                "impact_results": [],
                "skipped_invalid_targets": ["synchronized"],
                "skipped_missing_context_targets": ["MissingSymbol"],
                "skipped_missing_impact_targets": ["MissingSymbol"],
            },
            [],
        )

    assert any("synchronized" in item for item in report.limitations)
    assert any("MissingSymbol" in item for item in report.manual_verification)


def test_gitnexus_impact_service_surfaces_signature_change_manual_check(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/signature",
        target_ref="main",
        changed_files=["src/main/java/com/example/order/OrderService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/order/OrderService.java "
            "b/src/main/java/com/example/order/OrderService.java\n"
            "@@ -20,4 +20,4 @@\n"
            "-public Order create(String userId, BigDecimal amount) throws RetryableException {\n"
            "+public Order create(String userId, BigDecimal amount, Coupon coupon) {\n"
        ),
        metadata={"workspace_repo_path": str(repo_path)},
    )

    report = service._normalize_gitnexus_payload(
        subject,
        RuntimeSettings(code_repo_local_path=str(repo_path)),
        {"indexed_at": "2026-04-27T00:00:00+00:00", "commit": "abc123"},
        {
            "detect_changes": {},
            "context_results": [
                {
                    "symbol": {"name": "OrderService.create"},
                    "caller_contexts": [
                        {"path": "src/main/java/com/example/order/OrderController.java"},
                    ],
                }
            ],
            "impact_results": [],
        },
        [],
    )

    assert any("签名级变更" in item and "入参数量由 2 个变为 3 个" in item for item in report.manual_verification)
    assert any("OrderController.java" in item for item in report.manual_verification)


def test_gitnexus_impact_service_normalizes_mcp_fixture_payload(storage_root: Path):
    fixture_path = Path("backend/tests/fixtures/gitnexus_mcp/order-impact-payload.json")
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="order-service",
        project_id="proj_order",
        source_ref="feature/order-create",
        target_ref="main",
        changed_files=["src/main/java/com/example/order/OrderController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/order/OrderController.java "
            "b/src/main/java/com/example/order/OrderController.java\n"
            "@@ -12,3 +12,8 @@\n"
            "+public OrderResponse createOrder(@RequestBody CreateOrderRequest request) {\n"
            "+    Order order = applicationService.placeOrder(request.toCommand());\n"
            "+    return OrderResponse.from(order);\n"
            "+}\n"
        ),
    )

    report = service._normalize_gitnexus_payload(
        subject,
        RuntimeSettings(),
        {"state": "ready", "indexed_at": "2026-05-01T00:00:00+00:00", "commit": "fixture-commit"},
        raw,
        [],
    )

    assert report.graph_status == "ready"
    assert report.graph_commit == "fixture-commit"
    assert report.risk_level == "high"
    assert "OrderController.createOrder" in report.queried_targets
    assert report.successful_context_targets == ["OrderController.createOrder"]
    assert report.successful_impact_targets == ["OrderController.createOrder", "OrderApplicationService.placeOrder"]
    assert report.skipped_invalid_targets == ["synchronized"]
    assert report.skipped_missing_context_targets == ["MissingAuditClient.notify"]
    assert any(item.file_path.endswith("OrderApplicationService.java") for item in report.impacted_files)
    assert any(item.file_path.endswith("OrderRepository.java") for item in report.impacted_files)
    assert any(item.scope == "Order create API regression" for item in report.recommended_test_scope)
    assert any(item.scope == "Repository persistence regression" for item in report.recommended_test_scope)
    assert any(path.path == ["OrderController.createOrder", "OrderApplicationService.placeOrder", "OrderRepository.save"] for path in report.impact_paths)
    assert any(
        path.path == ["OrderController.createOrder", "OrderApplicationService.placeOrder", "OrderRepository.save"]
        and path.confidence_label == "confirmed"
        for path in report.impact_paths
    )
    assert any(path.confidence_label == "candidate" for path in report.impact_paths)
    assert any(path.path == ["OrderApplicationService.placeOrder", "order-domain"] for path in report.impact_paths)
    assert any(node.label == "OrderRepository.save" for node in report.impact_graph.nodes)
    assert any("MissingAuditClient.notify" in item for item in report.manual_verification)


def test_gitnexus_impact_service_applies_historical_impact_feedback(storage_root: Path):
    service = GitNexusImpactService(storage_root)
    review_service = ReviewService(storage_root=storage_root)
    review = review_service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "order-service",
            "project_id": "proj_order",
            "source_ref": "feature/order-impact-feedback",
            "target_ref": "main",
            "title": "impact feedback calibration seed",
            "changed_files": ["src/main/java/com/example/order/OrderController.java"],
        }
    )
    for index in range(3):
        review_service.record_impact_feedback(
            review.review_id,
            target_type="impact_path",
            target_key="OrderController.createOrder -> OrderApplicationService.placeOrder -> OrderRepository.save",
            label="false_positive",
            comment=f"第 {index} 次验证未命中",
        )
    raw = {
        "detect_changes": {},
        "impact_results": [
            {
                "target": "OrderController.createOrder",
                "impact_paths": [
                    [
                        "OrderController.createOrder",
                        "OrderApplicationService.placeOrder",
                        "OrderRepository.save",
                    ],
                    ["OrderController.createOrder", "AuditClient.notify"],
                ],
                "testRecommendations": [
                    {
                        "scope": "Repository persistence regression",
                        "reason": "Cover repository write behavior.",
                        "paths": [
                            "OrderController.createOrder -> OrderApplicationService.placeOrder -> OrderRepository.save"
                        ],
                        "priority": "high",
                    },
                    {
                        "scope": "Audit notification regression",
                        "reason": "Cover audit notification behavior.",
                        "paths": ["OrderController.createOrder -> AuditClient.notify"],
                        "priority": "high",
                    },
                ],
            }
        ],
        "context_results": [],
    }
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="order-service",
        project_id="proj_order",
        source_ref="feature/order-create",
        target_ref="main",
        changed_files=["src/main/java/com/example/order/OrderController.java"],
    )

    report = service._normalize_gitnexus_payload(
        subject,
        RuntimeSettings(),
        {"state": "ready", "indexed_at": "2026-05-01T00:00:00+00:00", "commit": "fixture-commit"},
        raw,
        [],
    )

    demoted_path = next(path for path in report.impact_paths if path.target == "OrderRepository.save")
    assert demoted_path.confidence_label == "needs_verification"
    assert "历史反馈显示该影响路径误报率 1.0" in demoted_path.confirmation_reason
    assert any("历史影响反馈要求人工复核" in item and "OrderRepository.save" in item for item in report.manual_verification)
    assert report.impact_paths[-1].target == "OrderRepository.save"
    persistence_scope = next(scope for scope in report.recommended_test_scope if scope.scope == "Repository persistence regression")
    assert "关联路径历史误报率 1.0" in persistence_scope.reason
    assert report.recommended_test_scope[-1].scope == "Repository persistence regression"


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
        report = service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert report.graph_status == "degraded"
    assert any("按官方 MCP 流程调用失败" in item for item in report.limitations)


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

    with patch("app.services.gitnexus_impact_service.shutil.which", return_value=None), patch(
        "app.services.command_resolver._resolve_with_system_where",
        return_value="",
    ):
        report = service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert report.graph_status == "degraded"
    assert any("未预装 GitNexus" in item for item in report.limitations)


def test_gitnexus_mcp_command_parses_windows_space_path(monkeypatch):
    monkeypatch.setenv("GITNEXUS_MCP_COMMAND", r'"C:\Program Files\GitNexus\gitnexus.exe" mcp')
    client = GitNexusMcpImpactClient()

    assert client._command() == [r"C:\Program Files\GitNexus\gitnexus.exe", "mcp"]


def test_gitnexus_mcp_command_accepts_json_array(monkeypatch):
    monkeypatch.setenv("GITNEXUS_MCP_COMMAND", r'["C:\\Program Files\\GitNexus\\gitnexus.exe", "mcp"]')
    client = GitNexusMcpImpactClient()

    assert client._command() == [r"C:\Program Files\GitNexus\gitnexus.exe", "mcp"]


def test_gitnexus_service_diagnostics_uses_gitnexus_bin(monkeypatch, storage_root: Path):
    monkeypatch.delenv("GITNEXUS_MCP_COMMAND", raising=False)
    monkeypatch.setenv("GITNEXUS_BIN", r"C:\Tools\GitNexus\gitnexus.exe")
    service = GitNexusImpactService(storage_root)

    assert service._gitnexus_command_for_diagnostics() == [r"C:\Tools\GitNexus\gitnexus.exe", "mcp"]


def test_gitnexus_windows_path_compare_matches_drive_case_and_slashes(storage_root: Path):
    service = GitNexusImpactService(storage_root)

    assert service._status_matches_repo_path({"repoPath": r"C:\Work\Repo"}, "c:/work/repo/")
    assert service._resolve_repo_name_from_registry(
        "c:/work/repo",
        [{"name": "repo-win", "path": r"C:\Work\Repo\\"}],
    ) == "repo-win"
    assert service._registry_paths_for_repo_name(
        "c:/work/repo",
        "repo-win",
        [{"name": "repo-win", "path": r"C:\Work\Repo"}],
    ) == ["c:/work/repo"]


def test_gitnexus_preflight_reports_ready_when_windows_sensitive_inputs_match(
    storage_root: Path,
    tmp_path: Path,
):
    repo_path = tmp_path / "Repo With Space"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        repo_path / ".gitnexus" / "index_status.json",
        {"state": "ready", "repo_path": str(repo_path), "repo_name": "repo-space", "commit": "abc123"},
    )
    registry_path = tmp_path / "registry.json"
    write_json(registry_path, [{"name": "repo-space", "path": str(repo_path)}])
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        metadata={"workspace_repo_path": str(repo_path), "gitnexus_registry_path": str(registry_path)},
    )

    with patch("app.services.gitnexus_impact_service.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
        result = service.preflight(subject, RuntimeSettings())

    assert result["status"] == "ready"
    assert {item["name"]: item["status"] for item in result["checks"]}["graph_status"] == "passed"
    assert result["recommended_actions"] == []


def test_gitnexus_preflight_finds_windows_npm_cmd_when_service_path_is_stale(
    storage_root: Path,
    tmp_path: Path,
    monkeypatch,
):
    repo_path = tmp_path / "Repo"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    (repo_path / ".gitnexus").mkdir()
    write_json(
        repo_path / ".gitnexus" / "index_status.json",
        {"state": "ready", "repo_path": str(repo_path), "repo_name": "repo", "commit": "abc123"},
    )
    registry_path = tmp_path / "registry.json"
    write_json(registry_path, [{"name": "repo", "path": str(repo_path)}])
    appdata = tmp_path / "AppData" / "Roaming"
    fake_bin = appdata / "npm" / "gitnexus.cmd"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr("app.services.command_resolver.shutil.which", lambda command: None)
    monkeypatch.setattr("app.services.gitnexus_impact_service.shutil.which", lambda command: "/usr/bin/git" if command == "git" else None)
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        metadata={"workspace_repo_path": str(repo_path), "gitnexus_registry_path": str(registry_path)},
    )

    result = service.preflight(subject, RuntimeSettings())

    checks = {item["name"]: item for item in result["checks"]}
    assert checks["gitnexus_command"]["status"] == "passed"
    assert str(fake_bin) in checks["gitnexus_command"]["message"]
    assert result["status"] == "ready"


def test_gitnexus_preflight_reports_actionable_failures(storage_root: Path):
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderController.java"],
        metadata={},
    )

    with patch("app.services.gitnexus_impact_service.shutil.which", return_value=None), patch(
        "app.services.command_resolver._resolve_with_system_where",
        return_value="",
    ):
        result = service.preflight(subject, RuntimeSettings())

    assert result["status"] == "failed"
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["git_binary"]["status"] == "failed"
    assert checks["gitnexus_command"]["status"] == "failed"
    assert checks["repo_path"]["status"] == "failed"
    assert result["recommended_actions"]


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
        report = service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert report.graph_status == "degraded"
    assert any("重复仓库名" in item for item in report.limitations)


def test_gitnexus_impact_service_self_heals_registry_when_local_graph_ready(storage_root: Path, tmp_path: Path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    (repo_b / ".gitnexus").mkdir()
    write_json(
        repo_b / ".gitnexus" / "meta.json",
        {
            "repoPath": str(repo_b),
            "lastCommit": "meta-commit",
            "indexedAt": "2026-04-28T00:00:00Z",
            "stats": {"files": 3},
        },
    )
    registry_path = tmp_path / ".gitnexus" / "registry.json"
    registry_path.parent.mkdir()
    write_json(registry_path, {"repositories": [{"name": "repo-a", "path": str(repo_a)}]})
    capture = CaptureGitNexusImpactClient()
    service = GitNexusImpactService(storage_root, mcp_client=capture)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-b",
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
        metadata={"workspace_repo_path": str(repo_b), "gitnexus_registry_path": str(registry_path)},
    )

    with (
        patch("app.services.gitnexus_impact_service.shutil.which", return_value="/usr/local/bin/gitnexus"),
        patch(
            "app.services.gitnexus_impact_service.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout='[{"name":"repo-a"},{"name":"repo-b"}]', stderr=""),
        ),
    ):
        report = service.analyze(subject, RuntimeSettings(code_repo_local_path=str(repo_b)))

    assert report.graph_status == "ready"
    assert capture.repo_name == "repo-b"
    assert capture.cli_available_repos == ["repo-a", "repo-b"]
    assert capture.runtime_env is not None
    assert capture.runtime_env["GITNEXUS_REGISTRY_PATH"] == str(registry_path)
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert [item["name"] for item in payload["repositories"]] == ["repo-a", "repo-b"]
    repo_b_entry = payload["repositories"][1]
    assert repo_b_entry["path"] == str(repo_b)
    assert repo_b_entry["storagePath"] == str(repo_b / ".gitnexus")
    assert repo_b_entry["lastCommit"] == "meta-commit"


def test_gitnexus_impact_service_uses_home_registry_path_for_mcp_env(storage_root: Path, tmp_path: Path):
    service = GitNexusImpactService(storage_root)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature",
        target_ref="main",
        changed_files=[],
        metadata={"workspace_repo_path": str(repo_path), "gitnexus_home": str(tmp_path)},
    )

    env = service._gitnexus_runtime_env(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert service._gitnexus_registry_path(subject, RuntimeSettings()).as_posix().endswith("/.gitnexus/registry.json")
    assert env is not None
    assert env["HOME"] == str(tmp_path)
    assert env["GITNEXUS_HOME"] == str(tmp_path)


def test_gitnexus_impact_service_parses_gitnexus_list_outputs(storage_root: Path):
    service = GitNexusImpactService(storage_root)

    assert service._parse_gitnexus_list_output('[{"name":"repo-a"},{"name":"repo-b"}]') == ["repo-a", "repo-b"]
    assert service._parse_gitnexus_list_output("Name | Path\nrepo-a | C:/a\nrepo-b | C:/b\n") == ["repo-a", "repo-b"]
    assert service._parse_gitnexus_list_output("- repo-a\n- repo-b\n") == ["repo-a", "repo-b"]


def test_gitnexus_impact_service_normalizes_dot_gitnexus_home_for_mcp_env(storage_root: Path, tmp_path: Path):
    service = GitNexusImpactService(storage_root)
    gitnexus_home = tmp_path / ".gitnexus"
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature",
        target_ref="main",
        changed_files=[],
        metadata={"gitnexus_home": str(gitnexus_home)},
    )

    env = service._gitnexus_runtime_env(subject, RuntimeSettings())

    assert service._gitnexus_registry_path(subject, RuntimeSettings()) == gitnexus_home / "registry.json"
    assert env is not None
    assert env["HOME"] == str(tmp_path)
    assert env["GITNEXUS_HOME"] == str(tmp_path)


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


def test_gitnexus_impact_service_prefers_platform_diff_without_local_git_diff(storage_root: Path, tmp_path: Path):
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
        patch.object(service, "_load_local_diff_from_git", side_effect=AssertionError("should not load local git diff")),
        patch.object(service, "_scan_changed_file_symbols", return_value=[]),
    ):
        symbols = service._build_changed_symbols(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    assert any(item.symbol == "createOrder" and item.container == "OrderController" for item in symbols)


def test_gitnexus_impact_service_extracts_removed_java_signatures_as_changed_symbols(storage_root: Path):
    service = GitNexusImpactService(storage_root)

    symbols = service._extract_changed_symbols(
        "diff --git a/src/main/java/com/example/OrderService.java "
        "b/src/main/java/com/example/OrderService.java\n"
        "@@ -12,7 +12,6 @@ public class OrderService {\n"
        " public class OrderService {\n"
        "-  public Order create(String userId, BigDecimal amount) throws RetryableException;\n"
        " }\n"
    )

    assert any(
        item.symbol == "create"
        and item.container == "OrderService"
        and item.kind == "function"
        and item.line_start == 13
        for item in symbols
    )


def test_gitnexus_impact_service_extracts_java_constructor_as_changed_symbol(storage_root: Path):
    service = GitNexusImpactService(storage_root)

    symbols = service._extract_changed_symbols(
        "diff --git a/src/main/java/com/example/OrderService.java "
        "b/src/main/java/com/example/OrderService.java\n"
        "@@ -12,7 +12,7 @@ public class OrderService {\n"
        " public class OrderService {\n"
        "-  public OrderService(PaymentClient paymentClient) {\n"
        "+  public OrderService(PaymentClient paymentClient, MeterRegistry meterRegistry) {\n"
        "   }\n"
    )

    assert any(
        item.symbol == "OrderService"
        and item.container == "OrderService"
        and item.kind == "function"
        and item.line_start == 13
        for item in symbols
    )


def test_gitnexus_impact_service_extracts_interface_method_signatures(storage_root: Path):
    service = GitNexusImpactService(storage_root)

    symbols = service._extract_changed_symbols(
        "diff --git a/src/main/java/com/example/OwnerRepository.java "
        "b/src/main/java/com/example/OwnerRepository.java\n"
        "@@ -8,3 +8,4 @@ public interface OwnerRepository {\n"
        " public interface OwnerRepository {\n"
        "+  List<Owner> findByLastNameContaining(String lastName);\n"
        " }\n"
    )

    assert any(
        item.symbol == "findByLastNameContaining"
        and item.container == "OwnerRepository"
        and item.kind == "function"
        for item in symbols
    )


def test_gitnexus_impact_service_extracts_symbols_from_diff_mentions_when_no_declaration(storage_root: Path):
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
            "+  orderService.createOrder(command);\n"
            "+  auditPublisher.publish(event);\n"
            " }\n"
        ),
    )

    symbols = service._build_changed_symbols(subject, RuntimeSettings())

    extracted = {item.symbol for item in symbols}
    assert "createOrder" in extracted
    assert "publish" in extracted


def test_gitnexus_impact_service_scans_source_before_noisy_diff_mentions(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/HibernateCriteriaConverter.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/HibernateCriteriaConverter.java "
            "b/src/main/java/com/example/HibernateCriteriaConverter.java\n"
            "@@ -60,7 +60,7 @@ public final class HibernateCriteriaConverter<T> {\n"
            "   private Predicate equalsPredicateTransformer(Filter filter, Root<T> root) {\n"
            "-    return builder.equal(root.get(filter.field().value()), filter.value().value());\n"
            "+    return builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()));\n"
            "   }\n"
        ),
        metadata={"workspace_repo_path": str(repo_path)},
    )

    with patch.object(
        service,
        "_scan_changed_file_symbols",
        return_value=[
            ImpactSymbol(
                file_path="src/main/java/com/example/HibernateCriteriaConverter.java",
                symbol="equalsPredicateTransformer",
                kind="function",
                container="HibernateCriteriaConverter",
                line_start=62,
            )
        ],
    ):
        symbols = service._build_changed_symbols(subject, RuntimeSettings(code_repo_local_path=str(repo_path)))

    targets = {f"{item.container}.{item.symbol}" if item.container else item.symbol for item in symbols}
    assert "HibernateCriteriaConverter.equalsPredicateTransformer" in targets
    assert not {"SELECT", "FROM", "String", "Integer"}.intersection({item.symbol for item in symbols})


def test_gitnexus_impact_service_marks_ready_graph_as_degraded_when_mcp_has_no_index(storage_root: Path):
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/api",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff="",
    )

    report = service._normalize_gitnexus_payload(
        subject,
        RuntimeSettings(),
        {"state": "ready", "commit": "abc123"},
        {
            "available_repos": [],
            "detect_changes_error": "Error: No indexed repositories. Run: gitnexus analyze",
            "queried_targets": ["OrderService.create"],
            "successful_context_targets": [],
            "successful_impact_targets": [],
            "context_results": [],
            "impact_results": [],
        },
        [],
    )

    assert report.graph_status == "degraded"
    assert any("候选影响分析" in item for item in report.limitations)
    assert any("按候选结果处理" in item for item in report.manual_verification)


def test_gitnexus_impact_service_extracts_synchronized_java_method_name(storage_root: Path):
    service = GitNexusImpactService(storage_root)
    symbols = service._extract_changed_symbols(
        "diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n"
        "@@ -1,0 +1,3 @@\n"
        " public class OrderService {\n"
        "+  public synchronized OrderDTO createOrder(Command command) {\n"
        "+  }\n"
    )

    assert any(item.symbol == "createOrder" for item in symbols)


def test_gitnexus_source_scan_includes_java_constructor(storage_root: Path):
    service = GitNexusImpactService(storage_root)

    symbols = service._extract_symbols_from_source(
        "src/main/java/com/example/OrderService.java",
        "\n".join(
            [
                "package com.example;",
                "public class OrderService {",
                "  public OrderService(PaymentClient paymentClient) {",
                "    this.paymentClient = paymentClient;",
                "  }",
                "  public OrderDTO createOrder(Command command) {",
                "    return new OrderDTO();",
                "  }",
                "}",
            ]
        ),
    )

    assert any(item.symbol == "OrderService" and item.container == "OrderService" for item in symbols)
    assert any(item.symbol == "createOrder" and item.container == "OrderService" for item in symbols)


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

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
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


def test_gitnexus_local_git_diff_uses_git_style_paths_for_windows_changed_files(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    service = GitNexusImpactService(storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_impact",
        project_id="proj_impact",
        source_ref="feature/windows-path",
        target_ref="main",
        commits=["abc123"],
        changed_files=[r"src\main\java\com\example\OrderController.java"],
        unified_diff="",
    )

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        if command[:3] == ["git", "rev-parse", "--verify"]:
            ref = command[3].replace("^{commit}", "")
            if ref in {"abc123", "origin/main"}:
                return type("Completed", (), {"returncode": 0, "stdout": f"{ref}\n", "stderr": ""})()
            return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "unknown revision"})()
        if command[:3] == ["git", "diff", "--unified=3"]:
            assert command[-1] == "src/main/java/com/example/OrderController.java"
            return type("Completed", (), {"returncode": 0, "stdout": "+demo\n", "stderr": ""})()
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

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
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


def test_gitnexus_load_file_content_uses_git_style_path(storage_root: Path, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    service = GitNexusImpactService(storage_root)

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        assert command == ["git", "show", "abc123:src/main/java/com/example/OrderController.java"]
        return type("Completed", (), {"returncode": 0, "stdout": "class OrderController {}\n", "stderr": ""})()

    with patch("app.services.gitnexus_impact_service.subprocess.run", side_effect=fake_run):
        content = service._load_file_content(
            str(repo_path),
            "abc123",
            r"src\main\java\com\example\OrderController.java",
        )

    assert "OrderController" in content


def test_gitnexus_builds_precise_must_run_test_commands(storage_root: Path):
    service = GitNexusImpactService(storage_root)

    commands = service._build_must_run_tests(
        [
            "order-service/src/main/java/com/acme/order/OrderController.java",
            "frontend/src/pages/OrderPage.tsx",
            "backend/app/orders/service.py",
        ]
    )

    assert "mvn test -pl order-service -Dtest=OrderControllerTest" in commands
    assert "mvnw.cmd test -pl order-service -Dtest=OrderControllerTest" in commands
    assert "./gradlew :order-service:test --tests \"*OrderControllerTest\"" in commands
    assert "gradlew.bat :order-service:test --tests \"*OrderControllerTest\"" in commands
    assert "npm test -- frontend/src/pages/OrderPage.test.tsx" in commands
    assert "npm run lint -- frontend/src/pages/OrderPage.tsx" in commands
    assert "pytest tests/backend/app/orders/test_service.py" in commands


def test_gitnexus_impact_uses_repository_specific_path_and_status(storage_root: Path, tmp_path: Path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    service = GitNexusImpactService(storage_root)
    runtime = RuntimeSettings(
        default_repository_id="repo-a",
        code_repositories=[
            CodeRepositorySettings(repository_id="repo-a", local_path=str(repo_a), clone_url="https://example.com/a.git"),
            CodeRepositorySettings(repository_id="repo-b", local_path=str(repo_b), clone_url="https://example.com/b.git"),
        ],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-b",
        project_id="proj",
        source_ref="feature/demo",
        target_ref="main",
        changed_files=["src/main/java/Demo.java"],
        metadata={"repository_id": "repo-b"},
    )
    write_json(
        storage_root / "gitnexus" / "repo-a" / "index_status.json",
        {"state": "ready", "repo_path": str(repo_a), "repo_name": "repo-a"},
    )
    write_json(
        storage_root / "gitnexus" / "repo-b" / "index_status.json",
        {"state": "ready", "repo_path": str(repo_b), "repo_name": "repo-b"},
    )

    repo_path = service._repo_path(subject, runtime)
    graph_status = service._load_graph_status(repo_path, subject=subject, runtime=runtime)

    assert repo_path == str(repo_b)
    assert graph_status["repo_name"] == "repo-b"
    assert graph_status["repo_path"] == str(repo_b)


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

    result = gateway.invoke_for_expert(
        expert,
        subject,
        RuntimeSettings(),
        file_path="src/main/java/com/example/OrderController.java",
        line_start=1,
    )

    assert result
    assert result[0]["tool_name"] == "gitnexus_impact_analysis"
    assert result[0]["impact_report"]["graph_status"] == "degraded"
