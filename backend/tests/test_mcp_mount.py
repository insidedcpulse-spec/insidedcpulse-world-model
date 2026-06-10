from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_mcp_mounted_and_root_routes_not_shadowed():
    with patch("app.main.init_pool", AsyncMock(return_value=AsyncMock())), \
         patch("app.main.close_pool", AsyncMock()), \
         patch("app.main.get_redis", return_value=AsyncMock()), \
         patch("app.main.close_redis", AsyncMock()), \
         patch("app.main.worker_loop", AsyncMock()):
        from app.main import app

        with TestClient(app) as client:
            r = client.get("/")
            assert r.status_code == 200
            assert r.json()["name"] == "InsideDCPulse"
            assert r.json()["status"] == "/status"

            r = client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert r.status_code == 200
            assert "InsideDCPulse" in r.text
