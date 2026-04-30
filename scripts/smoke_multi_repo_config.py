from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
WORK_ROOT = Path("/tmp/multi-codereview-agent-multi-repo-smoke")


def log(message: str) -> None:
    print(f"[multi-repo-smoke] {message}")


def run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed cwd={cwd} command={' '.join(command)}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )


def create_git_repo(path: Path, class_name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    source_dir = path / "src" / "main" / "java" / "demo"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / f"{class_name}.java").write_text(
        "\n".join(
            [
                "package demo;",
                "",
                f"public class {class_name} {{",
                "    public int calculate(int value) {",
                "        return value + 1;",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    run(["git", "init"], cwd=path)
    run(["git", "config", "user.email", "multi-repo-smoke@example.com"], cwd=path)
    run(["git", "config", "user.name", "multi-repo-smoke"], cwd=path)
    run(["git", "add", "."], cwd=path)
    run(["git", "commit", "-m", "initial commit"], cwd=path)


def create_client(storage_root: Path, config_path: Path) -> TestClient:
    os.environ["STORAGE_ROOT"] = str(storage_root)
    os.environ["CONFIG_PATH"] = str(config_path)
    sys.path.insert(0, str(BACKEND_ROOT))

    import app.config
    import app.services.review_service
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.services.review_service)
    importlib.reload(app.main)
    return TestClient(app.main.create_application())


def request_json(client: TestClient, method: str, path: str, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} failed status={response.status_code} body={response.text}")
    return response.json()


def main() -> int:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    repo_a = WORK_ROOT / "repos" / "order-service"
    repo_b = WORK_ROOT / "repos" / "inventory-service"
    storage_root = WORK_ROOT / "storage"
    config_path = WORK_ROOT / "config.json"

    log("creating two real local git repositories")
    create_git_repo(repo_a, "OrderService")
    create_git_repo(repo_b, "InventoryService")

    client = create_client(storage_root, config_path)

    repositories = [
        {
            "repository_id": "order-service",
            "name": "Order Service",
            "provider": "generic",
            "clone_url": "https://example.com/team/order-service.git",
            "web_url_prefixes": ["https://example.com/team/order-service"],
            "local_path": str(repo_a),
            "default_branch": "main",
            "enabled": True,
            "auto_review_enabled": True,
            "auto_review_poll_interval_seconds": 90,
            "auto_sync": False,
            "gitnexus_enabled": True,
            "database_source_ids": ["pg-order"],
        },
        {
            "repository_id": "inventory-service",
            "name": "Inventory Service",
            "provider": "generic",
            "clone_url": "https://example.com/team/inventory-service.git",
            "web_url_prefixes": ["https://example.com/team/inventory-service"],
            "local_path": str(repo_b),
            "default_branch": "release",
            "enabled": True,
            "auto_review_enabled": True,
            "auto_review_poll_interval_seconds": 180,
            "auto_sync": False,
            "gitnexus_enabled": True,
            "database_source_ids": ["pg-inventory"],
        },
    ]

    log("updating runtime settings through API")
    runtime = request_json(
        client,
        "PUT",
        "/api/settings/runtime",
        json={
            "default_target_branch": "main",
            "default_analysis_mode": "light",
            "default_repository_id": "order-service",
            "code_repositories": repositories,
            "auto_review_enabled": True,
            "auto_review_poll_interval_seconds": 120,
            "allow_human_gate": True,
        },
    )
    assert len(runtime["code_repositories"]) == 2
    assert runtime["default_repository_id"] == "order-service"
    assert runtime["code_repo_local_path"] == str(repo_a)
    assert runtime["auto_review_enabled"] is True
    log("runtime settings saved and legacy default fields mirror the default repository")

    log("checking per-repository GitNexus status endpoints")
    status_a = request_json(client, "GET", "/api/settings/repositories/order-service/gitnexus/status")
    status_b = request_json(client, "GET", "/api/settings/repositories/inventory-service/gitnexus/status")
    assert status_a["repository_id"] == "order-service"
    assert status_b["repository_id"] == "inventory-service"
    unknown = request_json(client, "POST", "/api/settings/repositories/unknown-repo/gitnexus/index/run")
    assert unknown["state"] == "skipped"
    assert unknown["repository_id"] == "unknown-repo"
    log("GitNexus status is isolated by repository_id and unknown repository does not fallback")

    log("checking queue sync sees both auto-review repositories before any pending review exists")
    queue_sync = request_json(client, "POST", "/api/reviews/queue/sync")
    queue_repos = {item["repository_id"] for item in queue_sync["repositories"]}
    assert queue_repos == {"order-service", "inventory-service"}
    assert queue_sync["started_review_id"] == ""
    log("queue sync scanned both configured auto-review repositories")

    log("creating MR review tasks for both repositories")
    review_a = request_json(
        client,
        "POST",
        "/api/reviews",
        json={
            "subject_type": "mr",
            "analysis_mode": "light",
            "repo_url": "https://example.com/team/order-service.git",
            "mr_url": "https://example.com/team/order-service/merge_requests/101",
            "source_ref": "feature/order-lock",
            "title": "Order Service MR",
            "changed_files": ["src/main/java/demo/OrderService.java"],
            "unified_diff": "diff --git a/src/main/java/demo/OrderService.java b/src/main/java/demo/OrderService.java\n@@ -4,3 +4,3 @@\n-        return value + 1;\n+        return value + 2;\n",
        },
    )
    review_b = request_json(
        client,
        "POST",
        "/api/reviews",
        json={
            "subject_type": "mr",
            "analysis_mode": "light",
            "repo_url": "https://example.com/team/inventory-service.git",
            "mr_url": "https://example.com/team/inventory-service/merge_requests/202",
            "source_ref": "feature/inventory-limit",
            "title": "Inventory Service MR",
            "changed_files": ["src/main/java/demo/InventoryService.java"],
            "unified_diff": "diff --git a/src/main/java/demo/InventoryService.java b/src/main/java/demo/InventoryService.java\n@@ -4,3 +4,3 @@\n-        return value + 1;\n+        return value + 3;\n",
        },
    )
    detail_a = request_json(client, "GET", f"/api/reviews/{review_a['review_id']}")
    detail_b = request_json(client, "GET", f"/api/reviews/{review_b['review_id']}")
    meta_a = detail_a["subject"]["metadata"]
    meta_b = detail_b["subject"]["metadata"]
    assert meta_a["repository_id"] == "order-service"
    assert meta_a["workspace_repo_path"] == str(repo_a)
    assert detail_a["subject"]["target_ref"] == "main"
    assert "change_impact_analysis" in detail_a["selected_experts"]
    assert meta_b["repository_id"] == "inventory-service"
    assert meta_b["workspace_repo_path"] == str(repo_b)
    assert detail_b["subject"]["target_ref"] == "release"
    assert "change_impact_analysis" in detail_b["selected_experts"]
    log("review creation resolved repo_url to the correct local path and default branch")

    print(
        "\n".join(
            [
                "",
                "SMOKE_RESULT=PASS",
                f"repo_a={repo_a}",
                f"repo_b={repo_b}",
                f"review_a={review_a['review_id']} repository_id={meta_a['repository_id']} target={detail_a['subject']['target_ref']}",
                f"review_b={review_b['review_id']} repository_id={meta_b['repository_id']} target={detail_b['subject']['target_ref']}",
                f"storage_root={storage_root}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
