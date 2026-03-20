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
            "tool_allowlist": ["local_diff", "schema_diff"],
            "mcp_allowlist": ["github.diff", "playwright.snapshot"],
            "runtime_tool_allowlist": ["frontend-design"],
            "agent_allowlist": ["judge"],
            "allow_human_gate": True,
            "issue_filter_enabled": True,
            "suppress_low_risk_hint_issues": True,
            "hint_issue_confidence_threshold": 0.9,
            "hint_issue_evidence_cap": 3,
            "default_max_debate_rounds": 3,
            "standard_llm_timeout_seconds": 75,
            "standard_llm_retry_count": 4,
            "standard_max_parallel_experts": 3,
            "light_llm_timeout_seconds": 180,
            "light_llm_retry_count": 2,
            "light_max_parallel_experts": 1,
            "light_max_debate_rounds": 1,
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
    assert payload["code_repo_access_token_configured"] is True
    assert payload["github_access_token_configured"] is True
    assert payload["gitlab_access_token_configured"] is True
    assert payload["codehub_access_token_configured"] is True
    assert payload["default_max_debate_rounds"] == 3
    assert payload["issue_filter_enabled"] is True
    assert payload["suppress_low_risk_hint_issues"] is True
    assert payload["hint_issue_confidence_threshold"] == 0.9
    assert payload["hint_issue_evidence_cap"] == 3
    assert payload["standard_llm_timeout_seconds"] == 75
    assert payload["standard_llm_retry_count"] == 4
    assert payload["standard_max_parallel_experts"] == 3
    assert payload["light_llm_timeout_seconds"] == 180
    assert payload["light_llm_retry_count"] == 2
    assert payload["light_max_parallel_experts"] == 1
    assert payload["light_max_debate_rounds"] == 1
    assert "schema_diff" in payload["tool_allowlist"]
    assert "frontend-design" in payload["runtime_tool_allowlist"]
    assert payload["default_llm_model"] == "kimi-k2.5"
    assert payload["default_llm_api_key_configured"] is True
    assert payload["verify_ssl"] is True
    assert payload["use_system_trust_store"] is True
    assert payload["ca_bundle_path"] == "C:/certs/corp-ca.pem"
    assert payload["config_path"].endswith("config.json")
    assert "default_llm_api_key" not in payload
