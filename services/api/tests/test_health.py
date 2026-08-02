from types import SimpleNamespace

from fastapi.testclient import TestClient
from app.main import app
from app.api.routes import health

client = TestClient(app)


def test_health_check(monkeypatch) -> None:
    # Mock database and Redis to be healthy for this test.
    async def mock_is_ready():
        return True

    monkeypatch.setattr(health, "is_db_ready", mock_is_ready)
    monkeypatch.setattr(health, "is_redis_ready", mock_is_ready)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(
            app_name="test-api",
            app_env="test",
            grammar_rag_enabled=False,
        ),
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["postgres"] is True
    assert response.json()["redis"] is True
    assert "overview_worker" not in response.json()
