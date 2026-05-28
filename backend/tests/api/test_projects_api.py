def test_projects_api_supports_create_select_and_bind_repositories(client):
    initial = client.get("/api/projects")
    assert initial.status_code == 200
    initial_payload = initial.json()
    assert initial_payload["default_project_id"]
    assert initial_payload["projects"]

    create = client.post(
        "/api/projects",
        json={
            "project_id": "pay-core",
            "name": "支付中台",
            "description": "支付核心链路代码检视项目",
            "owner_team": "支付研发团队",
            "status": "active",
            "repositories": [
                {
                    "repository_id": "ipc-fnd-service",
                    "name": "ipc-fnd-service",
                    "provider": "codehub",
                    "clone_url": "https://codehub.example.com/pay/ipc-fnd-service.git",
                    "web_url_prefixes": ["https://codehub.example.com/pay/ipc-fnd-service"],
                    "local_path": "/workspace/ipc-fnd-service",
                    "default_branch": "master",
                    "enabled": True,
                    "auto_review_enabled": True,
                    "auto_review_poll_interval_seconds": 120,
                    "auto_sync": False,
                    "gitnexus_enabled": True,
                    "database_source_ids": [],
                }
            ],
        },
    )
    assert create.status_code == 201
    project = create.json()
    assert project["project_id"] == "pay-core"
    assert project["repositories"][0]["repository_id"] == "ipc-fnd-service"

    selected = client.put("/api/projects/default/pay-core")
    assert selected.status_code == 200
    assert selected.json()["default_project_id"] == "pay-core"

    update_repos = client.put(
        "/api/projects/pay-core/repositories",
        json={
            "repositories": [
                {
                    "repository_id": "pay-web-console",
                    "name": "pay-web-console",
                    "provider": "gitlab",
                    "clone_url": "https://gitlab.example.com/pay/pay-web-console.git",
                    "web_url_prefixes": ["https://gitlab.example.com/pay/pay-web-console"],
                    "local_path": "/workspace/pay-web-console",
                    "default_branch": "main",
                    "enabled": True,
                    "auto_review_enabled": False,
                    "auto_review_poll_interval_seconds": 120,
                    "auto_sync": False,
                    "gitnexus_enabled": True,
                    "database_source_ids": [],
                }
            ]
        },
    )
    assert update_repos.status_code == 200
    assert update_repos.json()["repositories"][0]["repository_id"] == "pay-web-console"

    runtime = client.get("/api/settings/runtime").json()
    assert runtime["default_project_id"] == "pay-core"
    pay_core = next(item for item in runtime["projects"] if item["project_id"] == "pay-core")
    assert pay_core["repositories"][0]["repository_id"] == "pay-web-console"
    assert runtime["code_repositories"] == []

    update_project = client.put(
        "/api/projects/pay-core",
        json={
            "project_id": "pay-core",
            "name": "支付中台",
            "description": "支付核心链路代码检视项目",
            "owner_team": "支付研发团队",
            "status": "active",
            "repositories": [
                {
                    "repository_id": "pay-risk-engine",
                    "name": "pay-risk-engine",
                    "provider": "gitlab",
                    "clone_url": "https://gitlab.example.com/pay/pay-risk-engine.git",
                    "web_url_prefixes": ["https://gitlab.example.com/pay/pay-risk-engine"],
                    "local_path": "/workspace/pay-risk-engine",
                    "default_branch": "release",
                    "enabled": True,
                    "auto_review_enabled": False,
                    "auto_review_poll_interval_seconds": 120,
                    "auto_sync": False,
                    "gitnexus_enabled": True,
                    "database_source_ids": [],
                }
            ],
        },
    )
    assert update_project.status_code == 200

    runtime_after_project_update = client.get("/api/settings/runtime").json()
    assert runtime_after_project_update["default_project_id"] == "pay-core"
    pay_core_after_update = next(
        item for item in runtime_after_project_update["projects"] if item["project_id"] == "pay-core"
    )
    assert pay_core_after_update["repositories"][0]["repository_id"] == "pay-risk-engine"
    assert runtime_after_project_update["code_repositories"] == []
    assert runtime_after_project_update["default_repository_id"] == ""
