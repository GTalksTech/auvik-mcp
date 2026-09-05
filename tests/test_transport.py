"""--stdio selects stdio and routes logs to stderr; in HTTP mode
/health is open, everything else needs the right X-API-Key.

HTTP cases drive the app through starlette's TestClient used as a context
manager, deliberately rather than through httpx's ASGITransport.
That matters: entering the context runs the ASGI lifespan, which is what
initialises FastMCP's StreamableHTTPSessionManager task group. httpx's
ASGITransport does not run lifespan, so the MCP layer would raise instead of
answering — testing an app that was never started, not testing the auth wrapper.
uvicorn runs the lifespan in production, so this matches the real deployment.
"""
from __future__ import annotations

import sys

import pytest
from starlette.testclient import TestClient

import envelope as env

KEY = "inbound-secret"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
PING = {"jsonrpc": "2.0", "method": "ping", "id": 1}


def test_stdio_flag_sets_stderr_logging_and_runs_stdio(monkeypatch):
    import server

    seen = {}
    monkeypatch.setattr(server.mcp, "run", lambda transport: seen.setdefault("transport", transport))
    # main() starts the background warm thread and the mocked run returns at
    # once, so an unstubbed thread outlives this test and mutates global state
    # under later tests (the flake described in conftest._offline_guard).
    monkeypatch.setattr(server, "start_background_warm", lambda: None)
    server.main(["--stdio"])
    assert seen["transport"] == "stdio"
    assert env._log_stream is sys.stderr
    env.set_log_stream(sys.stdout)


def test_env_transport_stdio(monkeypatch):
    import server

    seen = {}
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setattr(server.mcp, "run", lambda transport: seen.setdefault("transport", transport))
    monkeypatch.setattr(server, "start_background_warm", lambda: None)  # see conftest._offline_guard
    server.main([])
    assert seen["transport"] == "stdio"
    env.set_log_stream(sys.stdout)


@pytest.fixture()
def http_client(monkeypatch):
    import server

    monkeypatch.setenv("MCP_INBOUND_API_KEY", KEY)
    with TestClient(server.build_app()) as c:
        yield c


def test_health_is_open(http_client):
    r = http_client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "app": "auvik-mcp"}


def test_missing_or_wrong_key_is_401(http_client):
    r1 = http_client.post("/mcp/", json=PING, headers=MCP_HEADERS)
    r2 = http_client.post("/mcp/", json=PING, headers={**MCP_HEADERS, "X-API-Key": "wrong"})
    assert r1.status_code == 401 and r2.status_code == 401


def test_correct_key_reaches_mcp_layer(http_client):
    r = http_client.post("/mcp/", json=PING, headers={**MCP_HEADERS, "X-API-Key": KEY})
    # reached the MCP layer (it may answer 200 or a protocol 4xx; not our 401/403)
    assert r.status_code not in (401, 403)


def test_unknown_path_without_key_is_401(http_client):
    assert http_client.get("/anything-else").status_code == 401


def test_unset_inbound_key_fails_closed(monkeypatch):
    import server

    monkeypatch.delenv("MCP_INBOUND_API_KEY", raising=False)
    with TestClient(server.build_app()) as c:
        # /health stays a liveness signal…
        assert c.get("/health").status_code == 200
        # …but the MCP surface refuses everything rather than opening up.
        r = c.post("/mcp/", json=PING, headers={**MCP_HEADERS, "X-API-Key": KEY})
        assert r.status_code == 401
