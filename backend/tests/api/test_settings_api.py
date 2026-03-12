def test_runtime_settings_can_be_read_and_updated(client):
    initial = client.get("/api/settings/runtime")
    assert initial.status_code == 200

    update = client.put(
        "/api/settings/runtime",
        json={
            "default_target_branch": "develop",
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
    assert payload["default_max_debate_rounds"] == 3
    assert "schema_diff" in payload["tool_allowlist"]
    assert "frontend-design" in payload["skill_allowlist"]
    assert payload["default_llm_model"] == "kimi-k2.5"
    assert payload["default_llm_api_key_configured"] is True
    assert "default_llm_api_key" not in payload
