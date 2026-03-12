def test_governance_endpoint_returns_quality_metrics(client):
    response = client.get("/api/governance/quality-metrics")

    assert response.status_code == 200
    payload = response.json()
    assert "tool_confirmation_rate" in payload
    assert "debate_survival_rate" in payload
