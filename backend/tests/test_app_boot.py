from fastapi.testclient import TestClient

from app.main import create_application


def test_health_endpoint_returns_ok():
    client = TestClient(create_application())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
