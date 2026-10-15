import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_general_api import FakeOllama, secure_client
from use_lllm.core.config import ConfigurationError
from use_lllm.core.sessions import SessionStore
from use_lllm.core.settings_store import (
    default_settings,
    load_settings,
    save_settings,
    settings_from_json,
    settings_to_json,
)
from use_lllm.general.api import create_general_app


def test_legacy_web_setting_defaults_off_and_round_trips():
    raw = settings_to_json(default_settings())
    raw.pop("web_search_enabled", None)
    assert not settings_from_json(raw).web_search_enabled
    raw["web_search_enabled"] = "false"
    with pytest.raises(ConfigurationError):
        settings_from_json(raw)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "settings.json"
        save_settings(path, replace(default_settings(), web_search_enabled=True))
        assert load_settings(path).web_search_enabled


@pytest.fixture
def app_client(tmp_path):
    app = create_general_app(
        session_store=SessionStore(tmp_path), ollama_factory=lambda config: FakeOllama()
    )
    with secure_client(app) as client:
        yield app, client


def test_updates_web_settings_with_csrf_and_strict_payload(app_client):
    app, client = app_client
    assert not client.get("/api/settings").json()["web_search_enabled"]
    assert client.put("/api/web-search", json={"enabled": True}).status_code == 200
    assert client.get("/api/tools").json()["tools"] == ["web::search", "web::read_page"]
    assert client.put("/api/web-search", json={"enabled": "false"}).status_code == 422
    assert client.put("/api/web-search", json={"enabled": True, "url": "x"}).status_code == 422
    assert (
        client.put(
            "/api/web-search", json={"enabled": False}, headers={"X-Use-LLLM-CSRF": "wrong"}
        ).status_code
        == 403
    )
    assert load_settings(app.state.runtime.settings_path).web_search_enabled


def test_busy_rejects_web_toggle_without_partial_save(app_client):
    app, client = app_client
    runtime = app.state.runtime
    runtime._active_turns.add("active")
    response = client.put("/api/web-search", json={"enabled": True})
    runtime._active_turns.clear()
    assert response.status_code == 409
    assert not runtime.settings.web_search_enabled


def test_collision_rejected_and_connection_mutations_preserve_setting(app_client):
    app, client = app_client
    assert client.put("/api/web-search", json={"enabled": True}).status_code == 200
    service = app.state.runtime.web_search
    endpoint = {"name": "other", "base_url": "http://127.0.0.1:11434", "default_model": "fake"}
    assert client.post("/api/endpoints", json=endpoint).status_code == 201
    assert client.post("/api/endpoints/other/select").status_code == 200
    assert client.put("/api/endpoints/other", json=endpoint).status_code == 200
    assert client.delete("/api/endpoints/other").status_code == 200
    assert app.state.runtime.web_search is service

    assert client.get("/api/settings").json()["web_search_enabled"]
    assert (
        client.post("/api/mcp-servers", json={"name": "srv", "command": "python"}).status_code
        == 201
    )
    assert (
        client.post("/api/mcp-servers", json={"name": "web", "command": "python"}).status_code
        == 409
    )
    assert (
        client.put("/api/mcp-servers/srv", json={"name": "web", "command": "python"}).status_code
        == 409
    )
    assert client.delete("/api/mcp-servers/srv").status_code == 200
    assert client.get("/api/settings").json()["web_search_enabled"]
    assert client.put("/api/web-search", json={"enabled": False}).status_code == 200
    assert client.get("/api/tools").json()["tools"] == []
    assert app.state.runtime.web_search is service


def test_web_toggle_preserves_connected_mcp(tmp_path):
    from contextlib import asynccontextmanager

    from tests.test_mcp_registry import FakeSession, FakeTool
    from use_lllm.core.mcp_registry import MCPRegistry

    @asynccontextmanager
    async def factory(spec):
        yield FakeSession(
            [FakeTool("peek", {"type": "object"}, annotations={"readOnlyHint": True})]
        )

    app = create_general_app(
        session_store=SessionStore(tmp_path),
        ollama_factory=lambda c: FakeOllama(),
        registry_factory=lambda specs: MCPRegistry(specs, session_factory=factory),
    )
    with secure_client(app) as client:
        client.post("/api/mcp-servers", json={"name": "fixture", "command": "python"})
        assert client.post("/api/mcp-servers/fixture/connect").status_code == 200
        delegate = app.state.runtime.registry
        assert client.put("/api/web-search", json={"enabled": True}).status_code == 200
        assert app.state.runtime.registry.delegate is delegate
        assert "fixture::peek" in client.get("/api/tools").json()["tools"]
        assert client.put("/api/web-search", json={"enabled": False}).status_code == 200
        assert app.state.runtime.registry is delegate


def test_dormant_web_preferences_do_not_block_remaining_tools(app_client):
    app, client = app_client
    from contextlib import asynccontextmanager

    from tests.test_mcp_registry import FakeSession, FakeTool
    from use_lllm.core.mcp_registry import MCPRegistry

    @asynccontextmanager
    async def factory(spec):
        yield FakeSession(
            [FakeTool("peek", {"type": "object"}, annotations={"readOnlyHint": True})]
        )

    app.state.runtime.registry_factory = lambda specs: MCPRegistry(specs, session_factory=factory)
    client.post("/api/mcp-servers", json={"name": "fixture", "command": "python"})
    client.post("/api/mcp-servers/fixture/connect")
    client.put("/api/web-search", json={"enabled": True})
    sid = client.post("/api/sessions", json={"title": "preferences"}).json()["id"]
    route = f"/api/sessions/{sid}/tools"
    assert client.put(route, json={"disabled_tools": ["web::search"]}).status_code == 200
    client.put("/api/web-search", json={"enabled": False})
    assert (
        client.put(route, json={"disabled_tools": ["web::search", "fixture::peek"]}).status_code
        == 200
    )
    client.put("/api/web-search", json={"enabled": True})
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["state"]["disabled_tools"] == ["fixture::peek", "web::search"]
    assert client.put(route, json={"disabled_tools": ["unknown::tool"]}).status_code == 400
