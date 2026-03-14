def test_create_custom_expert_and_list_it(client):
    response = client.post(
        "/api/experts",
        json={
            "expert_id": "frontend_accessibility",
            "name": "Frontend Accessibility Reviewer",
            "name_zh": "前端可访问性专家",
            "role": "frontend ux / a11y",
            "focus_areas": ["accessibility", "rendering", "state management"],
            "knowledge_sources": ["a11y-guidelines"],
            "tool_bindings": ["local_diff", "coverage_diff"],
            "mcp_tools": ["playwright.snapshot"],
            "runtime_tool_bindings": ["frontend-design"],
            "agent_bindings": ["judge"],
            "max_tool_calls": 4,
            "max_debate_rounds": 2,
            "provider": "dashscope-openai-compatible",
            "api_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "api_key_env": "DASHSCOPE_API_KEY",
            "model": "kimi-k2.5",
            "system_prompt": "Focus on accessibility regressions first.",
            "review_spec": "# 前端可访问性审视规范\n\n必须检查语义标签和键盘可访问性。",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["expert_id"] == "frontend_accessibility"
    assert payload["custom"] is True
    assert "frontend-design" in payload["runtime_tool_bindings"]
    assert payload["provider"] == "dashscope-openai-compatible"
    assert payload["model"] == "kimi-k2.5"
    assert "前端可访问性审视规范" in payload["review_spec"]

    experts = client.get("/api/experts").json()
    assert any(item["expert_id"] == "frontend_accessibility" for item in experts)


def test_update_expert_bindings(client):
    update = client.put(
        "/api/experts/security_compliance",
        json={
            "expert_id": "security_compliance",
            "name": "security-compliance",
            "name_zh": "安全与合规专家",
            "role": "关注权限、敏感信息和依赖风险",
            "enabled": True,
            "focus_areas": ["鉴权授权", "敏感数据", "输入校验"],
            "activation_hints": ["auth", "security"],
            "required_checks": ["权限边界是否被绕过"],
            "out_of_scope": ["不要代替性能专家判断容量瓶颈"],
            "preferred_artifacts": ["diff hunk"],
            "knowledge_sources": ["security-review-checklist", "auth-guideline"],
            "tool_bindings": ["local_diff"],
            "mcp_tools": [],
            "runtime_tool_bindings": ["knowledge_search", "diff_inspector"],
            "agent_bindings": ["main_agent", "judge"],
            "max_tool_calls": 2,
            "max_debate_rounds": 3,
            "provider": None,
            "api_base_url": None,
            "api_key_env": None,
            "model": None,
            "system_prompt": "你是安全与合规专家。",
            "review_spec": "# 安全与合规审视规范\n\n必须检查鉴权、输入校验和敏感数据暴露。",
        },
    )
    assert update.status_code == 200
    payload = update.json()
    assert "knowledge_search" in payload["runtime_tool_bindings"]
    assert "auth-guideline" in payload["knowledge_sources"]
    assert "安全与合规审视规范" in payload["review_spec"]
