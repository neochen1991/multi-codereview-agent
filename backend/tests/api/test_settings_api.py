def test_runtime_settings_can_be_read_and_updated(client):
    initial = client.get("/api/settings/runtime")
    assert initial.status_code == 200

    update = client.put(
        "/api/settings/runtime",
        json={
            "default_target_branch": "develop",
            "default_analysis_mode": "light",
            "code_repo_clone_url": "codehub-g.huawei.com/PIP/FND/projectname/merge_requests",
            "code_repo_local_path": "/tmp/example-repo",
            "code_repo_default_branch": "release",
            "code_repo_access_token": "ghp_example",
            "github_access_token": "ghp_github",
            "gitlab_access_token": "glpat_gitlab",
            "codehub_access_token": "codehub_token",
            "code_repo_auto_sync": True,
            "auto_review_enabled": True,
            "auto_review_poll_interval_seconds": 300,
            "database_sources": [
                {
                    "repo_url": "https://github.com/example/repo.git",
                    "provider": "postgres",
                    "host": "127.0.0.1",
                    "port": 5432,
                    "database": "app_review",
                    "user": "readonly",
                    "password_env": "APP_REVIEW_DB_PASSWORD",
                    "schema_allowlist": ["public", "audit"],
                    "ssl_mode": "require",
                    "connect_timeout_seconds": 6,
                    "statement_timeout_ms": 4000,
                    "enabled": True,
                }
            ],
            "tool_allowlist": ["local_diff", "schema_diff"],
            "mcp_allowlist": ["github.diff", "playwright.snapshot"],
            "runtime_tool_allowlist": ["frontend-design"],
            "agent_allowlist": ["judge"],
            "allow_human_gate": True,
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P1",
            "issue_confidence_threshold_p0": 0.99,
            "issue_confidence_threshold_p1": 0.95,
            "issue_confidence_threshold_p2": 0.82,
            "issue_confidence_threshold_p3": 0.71,
            "suppress_low_risk_hint_issues": True,
            "hint_issue_confidence_threshold": 0.9,
            "hint_issue_evidence_cap": 3,
            "enable_llm_evidence_filter": True,
            "llm_evidence_filter_confidence_threshold": 0.74,
            "llm_evidence_filter_timeout_seconds": 40,
            "enable_llm_issue_judge": True,
            "llm_issue_judge_confidence_threshold": 0.77,
            "llm_issue_judge_timeout_seconds": 44,
            "rule_screening_mode": "llm",
            "rule_screening_batch_size": 10,
            "rule_screening_llm_timeout_seconds": 150,
            "enable_llm_targeted_debate": True,
            "llm_targeted_debate_timeout_seconds": 80,
            "default_max_debate_rounds": 3,
            "standard_llm_timeout_seconds": 75,
            "standard_llm_retry_count": 4,
            "standard_max_parallel_experts": 3,
            "light_llm_timeout_seconds": 180,
            "light_llm_retry_count": 2,
            "light_max_parallel_experts": 1,
            "light_max_debate_rounds": 1,
            "light_llm_max_prompt_chars": 88000,
            "light_llm_max_input_tokens": 1500000,
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-sp-18ef22cce0a24275a54eb6d97574c366",
            "allow_llm_fallback": False,
            "verify_ssl": True,
            "use_system_trust_store": True,
            "ca_bundle_path": "C:/certs/corp-ca.pem",
        },
    )
    assert update.status_code == 200
    payload = update.json()
    assert payload["default_target_branch"] == "develop"
    assert payload["default_analysis_mode"] == "light"
    assert payload["code_repo_clone_url"] == "codehub-g.huawei.com/PIP/FND/projectname/merge_requests"
    assert payload["code_repo_local_path"] == "/tmp/example-repo"
    assert payload["code_repo_default_branch"] == "release"
    assert payload["code_repo_auto_sync"] is True
    assert payload["auto_review_enabled"] is True
    assert payload["auto_review_repo_url"] == "codehub-g.huawei.com/PIP/FND/projectname/merge_requests"
    assert payload["auto_review_poll_interval_seconds"] == 300
    assert len(payload["database_sources"]) == 1
    assert payload["database_sources"][0]["database"] == "app_review"
    assert payload["database_sources"][0]["schema_allowlist"] == ["public", "audit"]
    assert payload["code_repo_access_token_configured"] is True
    assert payload["github_access_token_configured"] is True
    assert payload["gitlab_access_token_configured"] is True
    assert payload["codehub_access_token_configured"] is True
    assert payload["default_max_debate_rounds"] == 3
    assert payload["issue_filter_enabled"] is True
    assert payload["issue_min_priority_level"] == "P1"
    assert payload["issue_confidence_threshold_p0"] == 0.99
    assert payload["issue_confidence_threshold_p1"] == 0.95
    assert payload["issue_confidence_threshold_p2"] == 0.82
    assert payload["issue_confidence_threshold_p3"] == 0.71
    assert payload["suppress_low_risk_hint_issues"] is True
    assert payload["hint_issue_confidence_threshold"] == 0.9
    assert payload["hint_issue_evidence_cap"] == 3
    assert payload["enable_llm_evidence_filter"] is True
    assert payload["llm_evidence_filter_confidence_threshold"] == 0.74
    assert payload["llm_evidence_filter_timeout_seconds"] == 40
    assert payload["enable_llm_issue_judge"] is True
    assert payload["llm_issue_judge_confidence_threshold"] == 0.77
    assert payload["llm_issue_judge_timeout_seconds"] == 44
    assert payload["rule_screening_mode"] == "llm"
    assert payload["rule_screening_batch_size"] == 10
    assert payload["rule_screening_llm_timeout_seconds"] == 150
    assert payload["enable_llm_targeted_debate"] is True
    assert payload["llm_targeted_debate_timeout_seconds"] == 80
    assert payload["standard_llm_timeout_seconds"] == 75
    assert payload["standard_llm_retry_count"] == 4
    assert payload["standard_max_parallel_experts"] == 3
    assert payload["light_llm_timeout_seconds"] == 180
    assert payload["light_llm_retry_count"] == 2
    assert payload["light_max_parallel_experts"] == 1
    assert payload["light_max_debate_rounds"] == 1
    assert payload["light_llm_max_prompt_chars"] == 88000
    assert payload["light_llm_max_input_tokens"] == 1500000
    assert "schema_diff" in payload["tool_allowlist"]
    assert "frontend-design" in payload["runtime_tool_allowlist"]
    assert payload["default_llm_model"] == "kimi-k2.5"
    assert payload["default_llm_api_key_configured"] is True
    assert payload["verify_ssl"] is True
    assert payload["use_system_trust_store"] is True
    assert payload["ca_bundle_path"] == "C:/certs/corp-ca.pem"
    assert payload["config_path"].endswith("config.json")
    assert "default_llm_api_key" not in payload


def test_sast_tools_status_exposes_install_and_runtime_state(client):
    response = client.get("/api/settings/sast-tools/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"enabled", "disabled"}
    assert isinstance(payload["command_tools"], list)
    assert isinstance(payload["report_tools"], list)
    semgrep = next(item for item in payload["command_tools"] if item["tool"] == "semgrep")
    assert semgrep["kind"] == "command"
    assert semgrep["category"] == "security"
    assert semgrep["status"] in {"available", "missing"}
    assert "windows" in semgrep["install"]
    assert semgrep["verify_commands"]
    spotbugs = next(item for item in payload["report_tools"] if item["tool"] == "spotbugs")
    assert spotbugs["kind"] == "report"
    assert spotbugs["status"] == "requires_report"
    assert spotbugs["report_paths"]


def test_gitnexus_preflight_endpoint_returns_diagnostics(client):
    response = client.get("/api/settings/gitnexus/preflight")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ready", "warning", "failed"}
    assert isinstance(payload["checks"], list)
    assert isinstance(payload["recommended_actions"], list)
    assert {item["name"] for item in payload["checks"]} >= {"git_binary", "gitnexus_command", "repo_path"}


def test_code_graph_status_endpoint_returns_tree_sitter_diagnostics(client):
    response = client.get("/api/settings/code-graph/index/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] in {"idle", "ready", "skipped", "unknown"}
    assert payload["repository_id"]
    assert isinstance(payload["dependency_checks"], list)
    assert {item["name"] for item in payload["dependency_checks"]} >= {"tree_sitter", "tree_sitter_java"}
