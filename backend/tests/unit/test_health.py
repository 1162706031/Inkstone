from fastapi.testclient import TestClient

from ai_platform.api.main import app


def test_health() -> None:
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
