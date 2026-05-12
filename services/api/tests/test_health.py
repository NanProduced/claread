from fastapi.testclient import TestClient
from app.main import app
from app.api.routes import health

client = TestClient(app)


def test_health_check(monkeypatch) -> None:
    # Mock database and worker to be healthy for this test
    async def mock_is_ready():
        return True

    monkeypatch.setattr(health, "is_db_ready", mock_is_ready)
    monkeypatch.setattr(health, "is_redis_ready", mock_is_ready)

    # Mock worker snapshot
    class MockWorker:
        def health_snapshot(self):
            return {"healthy": True, "inflight_tasks": 0}

    app.state.analysis_task_worker = MockWorker()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
