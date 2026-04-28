from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

from smoke_pg_review import API_BASE, poll_review, request_json


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return str(completed.stdout or "")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup_repo(root: Path, gitnexus_home: Path) -> tuple[Path, str, str]:
    repo = root / f"{root.name}-repo"
    repo.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "impact@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "impact-smoke"], cwd=repo)

    _write(
        repo / "src/main/java/com/example/order/OrderRepository.java",
        "package com.example.order;\n"
        "public class OrderRepository {\n"
        "    public void save(Order order) {}\n"
        "}\n",
    )
    _write(
        repo / "src/main/java/com/example/order/OrderApplicationService.java",
        "package com.example.order;\n"
        "public class OrderApplicationService {\n"
        "    private final OrderRepository repository = new OrderRepository();\n"
        "    public Order placeOrder(CreateOrderCommand command) {\n"
        "        Order order = new Order(command.id());\n"
        "        repository.save(order);\n"
        "        return order;\n"
        "    }\n"
        "}\n",
    )
    _write(
        repo / "src/main/java/com/example/order/OrderController.java",
        "package com.example.order;\n"
        "public class OrderController {\n"
        "    private final OrderApplicationService applicationService = new OrderApplicationService();\n"
        "    public OrderResponse createOrder(CreateOrderRequest request) {\n"
        "        Order order = applicationService.placeOrder(request.toCommand());\n"
        "        return OrderResponse.from(order);\n"
        "    }\n"
        "}\n",
    )
    _write(
        repo / "src/test/java/com/example/order/OrderControllerTest.java",
        "package com.example.order;\npublic class OrderControllerTest {}\n",
    )
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "baseline"], cwd=repo)
    target_ref = _run(["git", "rev-parse", "HEAD"], cwd=repo).strip()

    _run(["git", "checkout", "-b", "feature-impact"], cwd=repo)
    _write(
        repo / "src/main/java/com/example/order/OrderController.java",
        "package com.example.order;\n"
        "public class OrderController {\n"
        "    private final OrderApplicationService applicationService = new OrderApplicationService();\n"
        "    public OrderResponse createOrder(CreateOrderRequest request) {\n"
        "        Order order = applicationService.placeOrder(request.toCommand());\n"
        "        notifyAudit(order);\n"
        "        return OrderResponse.from(order);\n"
        "    }\n"
        "    private void notifyAudit(Order order) {\n"
        "        AuditPublisher.publish(order.id());\n"
        "    }\n"
        "}\n",
    )
    _write(
        repo / "src/main/java/com/example/order/OrderApplicationService.java",
        "package com.example.order;\n"
        "public class OrderApplicationService {\n"
        "    private final OrderRepository repository = new OrderRepository();\n"
        "    public Order placeOrder(CreateOrderCommand command) {\n"
        "        Order order = buildOrder(command);\n"
        "        repository.save(order);\n"
        "        return order;\n"
        "    }\n"
        "    private Order buildOrder(CreateOrderCommand command) {\n"
        "        return new Order(command.id());\n"
        "    }\n"
        "}\n",
    )
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "feature impact change"], cwd=repo)
    source_ref = _run(["git", "rev-parse", "HEAD"], cwd=repo).strip()

    env = dict(os.environ)
    env["HOME"] = str(gitnexus_home)
    env["USERPROFILE"] = str(gitnexus_home)
    env["GITNEXUS_HOME"] = str(gitnexus_home)
    _run(["gitnexus", "analyze"], cwd=repo, env=env)
    return repo, target_ref, source_ref


def main() -> int:
    health = request_json("GET", "http://127.0.0.1:8011/health")
    assert isinstance(health, dict) and health.get("status") == "ok"

    with tempfile.TemporaryDirectory(prefix="gitnexus-real-api-") as tmp:
        root = Path(tmp)
        gitnexus_home = root / "gitnexus-home"
        gitnexus_home.mkdir(parents=True, exist_ok=True)
        repo, target_ref, source_ref = _setup_repo(root, gitnexus_home)
        payload = {
            "subject_type": "branch",
            "analysis_mode": "standard",
            "repo_id": "repo_gitnexus_real",
            "project_id": "proj_gitnexus_real",
            "source_ref": source_ref,
            "target_ref": target_ref,
            "title": "GitNexus real impact smoke",
            "selected_experts": ["change_impact_analysis"],
            "changed_files": [
                "src/main/java/com/example/order/OrderController.java",
                "src/main/java/com/example/order/OrderApplicationService.java",
            ],
            "metadata": {
                "workspace_repo_path": str(repo),
                "gitnexus_home": str(gitnexus_home),
            },
        }
        created = request_json("POST", f"{API_BASE}/reviews", payload)
        assert isinstance(created, dict)
        review_id = str(created["review_id"])
        request_json("POST", f"{API_BASE}/reviews/{urllib.parse.quote(review_id)}/start")
        replay = poll_review(review_id, 240)
        report = request_json("GET", f"{API_BASE}/reviews/{urllib.parse.quote(review_id)}/report")
        assert isinstance(report, dict)
        impact_report = report.get("impact_report") or {}
        assert isinstance(impact_report, dict)
        graph = impact_report.get("impact_graph") or {}
        assert isinstance(graph, dict)
        output = {
            "review_id": review_id,
            "status": replay.get("review", {}).get("status") if isinstance(replay.get("review"), dict) else "",
            "phase": replay.get("review", {}).get("phase") if isinstance(replay.get("review"), dict) else "",
            "graph_status": impact_report.get("graph_status"),
            "changed_symbols": impact_report.get("changed_symbols"),
            "impact_path_count": len(impact_report.get("impact_paths") or []),
            "impact_graph_node_count": len(graph.get("nodes") or []),
            "impact_graph_edge_count": len(graph.get("edges") or []),
            "recommended_test_scope": [item.get("scope") for item in list(impact_report.get("recommended_test_scope") or []) if isinstance(item, dict)],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
