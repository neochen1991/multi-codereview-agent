def test_governance_endpoint_returns_quality_metrics(client):
    response = client.get("/api/governance/quality-metrics")

    assert response.status_code == 200
    payload = response.json()
    assert "tool_confirmation_rate" in payload
    assert "debate_survival_rate" in payload


def test_governance_endpoint_returns_llm_timeout_metrics(client):
    response = client.get("/api/governance/llm-timeout-metrics")

    assert response.status_code == 200
    payload = response.json()
    assert "timeout_count" in payload
    assert "recent_timeouts" in payload
