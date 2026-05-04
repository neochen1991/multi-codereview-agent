from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from app.repositories.fs import write_json
from app.services.gitnexus_impact_service import GitNexusImpactService
from app.services.review_service import ReviewService


class FakeGitNexusImpactClient:
    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols, runtime_env=None):
        return {
            "detect_changes": {
                "affected_files": [
                    {
                        "path": "src/main/java/com/example/order/OrderApplicationService.java",
                        "relationship": "caller",
                        "reason": "OrderController.create 调用了 OrderApplicationService.placeOrder。",
                        "riskLevel": "high",
                    },
                    {
                        "path": "src/test/java/com/example/order/OrderControllerTest.java",
                        "relationship": "test_candidate",
                        "reason": "入口链路测试需要回归。",
                        "riskLevel": "medium",
                    },
                ],
                "affected_processes": ["order-service"],
                "testRecommendations": [
                    {
                        "scope": "下单主链路接口回归",
                        "reason": "Controller -> ApplicationService -> Repository 链路被影响。",
                        "paths": [
                            "src/test/java/com/example/order/OrderControllerTest.java",
                            "src/test/java/com/example/order/OrderApplicationServiceTest.java",
                        ],
                        "priority": "high",
                    }
                ],
            },
            "impact_results": [
                {
                    "paths": [[
                        "OrderController.createOrder",
                        "OrderApplicationService.placeOrder",
                        "OrderRepository.save",
                    ]]
                }
            ],
        }


class FailingGitNexusImpactClient:
    def analyze_mr(self, *, repo_name, repo_path, subject, changed_symbols, runtime_env=None):
        raise RuntimeError("simulated mcp failure")


def _run(cmd: str) -> None:
    import subprocess

    subprocess.run(cmd, shell=True, check=True, text=True)


def _prepare_repo(root: Path) -> Path:
    repo_path = root / "repo"
    (repo_path / "src/main/java/com/example/order").mkdir(parents=True)
    (repo_path / "src/test/java/com/example/order").mkdir(parents=True)
    _run(f"git init -b main {repo_path}")
    _run(f"git -C {repo_path} config user.email test@example.com")
    _run(f"git -C {repo_path} config user.name test")
    (repo_path / "src/main/java/com/example/order/OrderRepository.java").write_text(
        "package com.example.order;\n"
        "public class OrderRepository {\n"
        "    public void save(String orderId) {}\n"
        "}\n",
        encoding="utf-8",
    )
    (repo_path / "src/main/java/com/example/order/OrderApplicationService.java").write_text(
        "package com.example.order;\n"
        "public class OrderApplicationService {\n"
        "    private final OrderRepository repository = new OrderRepository();\n"
        "    public String placeOrder(String cmd) {\n"
        "        repository.save(cmd);\n"
        "        return cmd;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (repo_path / "src/main/java/com/example/order/OrderController.java").write_text(
        "package com.example.order;\n"
        "public class OrderController {\n"
        "    private final OrderApplicationService applicationService = new OrderApplicationService();\n"
        "    public String createOrder(String request) {\n"
        "        return applicationService.placeOrder(request);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (repo_path / "src/test/java/com/example/order/OrderControllerTest.java").write_text(
        "package com.example.order;\npublic class OrderControllerTest {}\n",
        encoding="utf-8",
    )
    _run(f"git -C {repo_path} add .")
    _run(f"git -C {repo_path} commit -m init")
    (repo_path / ".gitnexus").mkdir()
    return repo_path


def _prepare_registry(root: Path, repo_path: Path) -> Path:
    registry_dir = root / ".gitnexus"
    registry_dir.mkdir(exist_ok=True)
    registry_path = registry_dir / "registry.json"
    write_json(
        registry_path,
        {
            "repositories": [
                {
                    "name": "repo",
                    "path": str(repo_path),
                }
            ]
        },
    )
    return registry_path


def _build_review_payload(repo_path: Path) -> dict[str, object]:
    return {
        "subject_type": "mr",
        "repo_id": "repo_java_demo",
        "project_id": "proj_java_demo",
        "source_ref": "feature/order-create",
        "target_ref": "main",
        "title": "Order create API adds application-service dispatch",
        "changed_files": ["src/main/java/com/example/order/OrderController.java"],
        "unified_diff": (
            "diff --git a/src/main/java/com/example/order/OrderController.java "
            "b/src/main/java/com/example/order/OrderController.java\n"
            "@@ -12,3 +12,8 @@\n"
            "+@PostMapping(\"/orders\")\n"
            "+public OrderResponse createOrder(@RequestBody CreateOrderRequest request) {\n"
            "+    Order order = applicationService.placeOrder(request.toCommand());\n"
            "+    return OrderResponse.from(order);\n"
            "+}\n"
        ),
        "selected_experts": ["change_impact_analysis"],
        "mr_url": "https://example.com/mr/123",
        "metadata": {"workspace_repo_path": str(repo_path)},
    }


def _run_case(name: str, client) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="impact-demo-") as tmp:
        root = Path(tmp)
        storage_root = root / "storage"
        repo_path = _prepare_repo(root)
        registry_path = _prepare_registry(root, repo_path)
        write_json(
            storage_root / "gitnexus" / "index_status.json",
            {
                "state": "ready",
                "repo_path": str(repo_path),
                "repo_name": "repo",
                "indexed_at": "2026-04-27T00:00:00+00:00",
                "commit": "abc123demo",
                "graph_dir": str(repo_path / ".gitnexus"),
                "graph_dir_exists": True,
            },
        )
        service = ReviewService(storage_root=storage_root)
        service.gitnexus_impact_service = GitNexusImpactService(storage_root, mcp_client=client)
        service.runner.gitnexus_impact_service = service.gitnexus_impact_service
        service.runtime_settings_service.update(
            {
                "code_repo_local_path": str(repo_path),
                "allow_llm_fallback": True,
            }
        )
        import os

        original_home_env = os.environ.get("HOME")
        os.environ["HOME"] = str(root)
        try:
            review = service.create_review(_build_review_payload(repo_path))
            review = service.start_review(review.review_id)
            report = service.build_report(review.review_id)
            impact = report.impact_report
            return {
                "case": name,
                "review_status": review.status,
                "review_phase": review.phase,
                "impact_progress": dict(review.subject.metadata.get("impact_analysis_progress") or {}),
                "registry_path": str(registry_path),
                "graph_status": impact.graph_status if impact else None,
                "graph_commit": impact.graph_commit if impact else None,
                "risk_level": impact.risk_level if impact else None,
                "impacted_files": [item.file_path for item in (impact.impacted_files[:4] if impact else [])],
                "recommended_test_scope": [item.scope for item in (impact.recommended_test_scope[:4] if impact else [])],
                "impact_paths": [item.path for item in (impact.impact_paths[:2] if impact else [])],
                "limitations": impact.limitations[:3] if impact else [],
            }
        finally:
            if original_home_env is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = original_home_env


def _assert_smoke_results(results: list[dict[str, object]]) -> None:
    by_case = {str(item.get("case") or ""): item for item in results}
    success = by_case.get("graph_ready_success") or {}
    failure = by_case.get("graph_ready_but_mcp_failed") or {}

    errors: list[str] = []
    if success.get("review_status") != "completed":
        errors.append(f"success case review_status expected completed, got {success.get('review_status')}")
    if success.get("graph_status") != "ready":
        errors.append(f"success case graph_status expected ready, got {success.get('graph_status')}")
    if not success.get("impact_paths"):
        errors.append("success case expected at least one GitNexus impact path")
    if "下单主链路接口回归" not in list(success.get("recommended_test_scope") or []):
        errors.append("success case expected GitNexus recommended test scope")
    if dict(success.get("impact_progress") or {}).get("state") != "completed":
        errors.append(f"success case impact progress expected completed, got {success.get('impact_progress')}")

    if failure.get("review_status") != "completed":
        errors.append(f"failure case review_status expected completed, got {failure.get('review_status')}")
    if dict(failure.get("impact_progress") or {}).get("state") != "failed":
        errors.append(f"failure case impact progress expected failed, got {failure.get('impact_progress')}")
    if failure.get("graph_status") is not None:
        errors.append(f"failure case should not expose fallback impact report, got graph_status={failure.get('graph_status')}")
    if failure.get("impact_paths"):
        errors.append("failure case should not expose fallback impact paths")

    if errors:
        raise AssertionError("\n".join(errors))


def main() -> None:
    results = [
        _run_case("graph_ready_success", FakeGitNexusImpactClient()),
        _run_case("graph_ready_but_mcp_failed", FailingGitNexusImpactClient()),
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    try:
        _assert_smoke_results(results)
    except AssertionError as error:
        print(f"smoke_gitnexus_impact_demo failed:\n{error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
