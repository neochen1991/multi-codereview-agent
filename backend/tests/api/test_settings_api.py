def test_runtime_settings_can_be_read_and_updated(client):
    initial = client.get("/api/settings/runtime")
    assert initial.status_code == 200

    update = client.put(
        "/api/settings/runtime",
        json={
            "default_target_branch": "develop",
            "code_repo_clone_url": "https://github.com/example/repo.git",
            "code_repo_local_path": "/tmp/example-repo",
            "code_repo_default_branch": "release",
            "code_repo_access_token": "ghp_example",
            "code_repo_auto_sync": True,
            "tool_allowlist": ["local_diff", "schema_diff"],
            "mcp_allowlist": ["github.diff", "playwright.snapshot"],
            "skill_allowlist": ["frontend-design"],
            "agent_allowlist": ["judge"],
            "allow_human_gate": True,
            "default_max_debate_rounds": 3,
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-sp-18ef22cce0a24275a54eb6d97574c366",
            "allow_llm_fallback": False,
        },
    )
    assert update.status_code == 200
    payload = update.json()
    assert payload["default_target_branch"] == "develop"
    assert payload["code_repo_clone_url"] == "https://github.com/example/repo.git"
    assert payload["code_repo_local_path"] == "/tmp/example-repo"
    assert payload["code_repo_default_branch"] == "release"
    assert payload["code_repo_auto_sync"] is True
    assert payload["code_repo_access_token_configured"] is True
    assert payload["default_max_debate_rounds"] == 3
    assert "schema_diff" in payload["tool_allowlist"]
    assert "frontend-design" in payload["skill_allowlist"]
    assert payload["default_llm_model"] == "kimi-k2.5"
    assert payload["default_llm_api_key_configured"] is True
    assert "default_llm_api_key" not in payload
