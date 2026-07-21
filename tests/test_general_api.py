from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from use_lllm.app import create_app as create_standalone_app
from use_lllm.core.mcp_client import MCPToolResult
from use_lllm.core.ollama import OllamaResponse
from use_lllm.core.policy import ToolDecision, ToolSafety
from use_lllm.core.sessions import SessionStore
from use_lllm.general.api import create_general_app


def assistant(content="", tool=None, arguments=None):
    message = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [{"function": {"name": tool, "arguments": arguments or {}}}]
    return OllamaResponse(message, "fake", None, None, None)


class FakeOllama:
    def __init__(self):
        self.responses = [assistant("こんにちは")]

    async def chat(self, messages, **kwargs):
        return self.responses.pop(0)

    async def health(self):
        return {
            "status": "ready",
            "base_url": "http://127.0.0.1:11434",
            "selected_model": "fake",
            "models": ["fake"],
        }

    async def pull_model(self, model):
        yield {"status": "pulling manifest"}
        yield {"status": "success", "completed": 1, "total": 1}


class FakeRegistry:
    def __init__(self, specs):
        self.specs = list(specs)
        self.connected = set()

    async def connect_autostart(self):
        return []

    async def connect(self, name):
        self.connected.add(name)
        return self.status(name)

    async def disconnect(self, name):
        self.connected.discard(name)
        return self.status(name)

    def status(self, name):
        return {
            "name": name,
            "status": "connected" if name in self.connected else "disconnected",
            "tool_count": 1 if name in self.connected else 0,
            "read_only_auto": False,
            "server": name if name in self.connected else None,
            "version": "1",
            "error": None,
        }

    def statuses(self):
        return [self.status(spec.name) for spec in self.specs]

    def tool_names(self):
        return [f"{name}::danger" for name in sorted(self.connected)]

    def ollama_tools(self, query=None, *, limit=12):
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "fake",
                    "parameters": {"type": "object"},
                },
            }
            for name in self.tool_names()
        ]

    def decide(self, name, *, approved=False, network_mode="offline"):
        return ToolDecision(
            name,
            ToolSafety.UNKNOWN,
            approved,
            True,
            "承認が必要です。" if not approved else "承認済みです。",
        )

    async def call_tool(
        self,
        name,
        arguments=None,
        *,
        approved=False,
        network_mode="offline",
        session_id=None,
    ):
        return MCPToolResult(
            name,
            False,
            ({"type": "text", "text": "tool-result"},),
            None,
        )


class GeneralApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root / "state")
        self.ollama = FakeOllama()
        self.registries = []

        def registry_factory(specs):
            registry = FakeRegistry(specs)
            self.registries.append(registry)
            return registry

        self.app = create_general_app(
            session_store=self.store,
            settings_path=self.root / "settings.json",
            ollama_factory=lambda _config: self.ollama,
            registry_factory=registry_factory,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_settings_endpoint_add_select_and_persist_lan_endpoint(self) -> None:
        with TestClient(self.app) as client:
            initial = client.get("/api/settings").json()
            self.assertEqual(initial["selected_endpoint"], "local")
            response = client.post(
                "/api/endpoints",
                json={
                    "name": "lab",
                    "base_url": "http://10.242.145.97:11434",
                    "trust": "lan_allowed",
                    "default_model": "qwen3:14b",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            selected = client.post("/api/endpoints/lab/select")
            self.assertEqual(selected.status_code, 200, selected.text)
            self.assertEqual(selected.json()["selected_endpoint"], "lab")

        self.assertTrue((self.root / "settings.json").is_file())
        reloaded = create_general_app(
            session_store=self.store,
            settings_path=self.root / "settings.json",
            ollama_factory=lambda _config: self.ollama,
            registry_factory=FakeRegistry,
        )
        with TestClient(reloaded) as client:
            self.assertEqual(client.get("/api/settings").json()["selected_endpoint"], "lab")

    def test_mcp_crud_connect_and_tools(self) -> None:
        with TestClient(self.app) as client:
            created = client.post(
                "/api/mcp-servers",
                json={
                    "name": "other",
                    "command": "python",
                    "args": ["server.py"],
                    "read_only_auto": False,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            connected = client.post("/api/mcp-servers/other/connect")
            self.assertEqual(connected.json()["status"], "connected")
            self.assertIn("other::danger", client.get("/api/tools").json()["tools"])
            deleted = client.delete("/api/mcp-servers/other")
            self.assertEqual(deleted.status_code, 200, deleted.text)

    def test_first_run_setup_has_optional_mcp_and_persists_completion(self) -> None:
        with TestClient(self.app) as client:
            initial = client.get("/api/setup")
            self.assertEqual(initial.status_code, 200, initial.text)
            self.assertFalse(initial.json()["completed"])
            self.assertTrue(initial.json()["mcp_optional"])
            self.assertEqual(client.get("/api/settings").json()["mcp_servers"], [])

            pulled = client.post("/api/setup/models/pull", json={"model": "fake"})
            self.assertEqual(pulled.status_code, 200, pulled.text)
            self.assertIn('"done": true', pulled.text)

            completed = client.post("/api/setup/complete", json={"model": "fake"})
            self.assertEqual(completed.status_code, 200, completed.text)
            self.assertTrue(completed.json()["completed"])
            self.assertTrue((self.store.base_directory / "setup.json").is_file())
            self.assertTrue(client.get("/api/setup").json()["completed"])

    def test_http_mcp_and_local_knowledge_attachment(self) -> None:
        with TestClient(self.app) as client:
            created = client.post(
                "/api/mcp-servers",
                json={
                    "name": "remote",
                    "transport": "streamable_http",
                    "url": "https://example.invalid/mcp",
                    "auth_mode": "oauth",
                    "headers": {"X-Workspace": "local"},
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            server = next(
                item for item in created.json()["mcp_servers"] if item["name"] == "remote"
            )
            self.assertEqual(server["transport"], "streamable_http")
            self.assertEqual(server["header_keys"], ["X-Workspace"])

            document = client.post(
                "/api/knowledge",
                json={
                    "name": "notes.txt",
                    "mime_type": "text/plain",
                    "content_base64": base64.b64encode("局所知識です".encode()).decode(),
                },
            )
            self.assertEqual(document.status_code, 201, document.text)
            session = client.post("/api/sessions", json={"title": "knowledge"}).json()
            answer = client.post(
                f"/api/sessions/{session['id']}/chat",
                json={"message": "資料を確認", "attachment_ids": [document.json()["id"]]},
            )
            self.assertEqual(answer.status_code, 200, answer.text)
            stored = client.get(f"/api/sessions/{session['id']}").json()["messages"][0]
            self.assertEqual(stored["metadata"]["attachment_ids"], [document.json()["id"]])
            self.assertIn("局所知識です", stored["metadata"]["attachment_context"])

    def test_general_sessions_are_separate_and_plain_chat_works(self) -> None:
        self.store.create_session("analysis")
        with TestClient(self.app) as client:
            created = client.post("/api/sessions", json={"title": "chat"}).json()
            self.assertEqual(created["surface"], "general")
            self.assertEqual(len(client.get("/api/sessions").json()), 1)
            result = client.post(
                f"/api/sessions/{created['id']}/chat",
                json={"message": "こんにちは"},
            )
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["content"], "こんにちは")

    def test_approval_endpoint_executes_pending_tool_and_resumes(self) -> None:
        self.ollama.responses = [
            assistant(tool="ms-data-parser::danger", arguments={"x": 1}),
            assistant("完了"),
        ]
        with TestClient(self.app) as client:
            client.post(
                "/api/mcp-servers",
                json={"name": "ms-data-parser", "command": "python", "args": ["server.py"]},
            )
            client.post("/api/mcp-servers/ms-data-parser/connect")
            session = client.post("/api/sessions", json={"title": "chat"}).json()
            pending = client.post(
                f"/api/sessions/{session['id']}/chat",
                json={"message": "実行"},
            ).json()
            self.assertEqual(pending["status"], "approval_required")
            resolved = client.post(
                f"/api/sessions/{session['id']}/approvals/{pending['approval']['event_id']}",
                json={"approved": True},
            )
            self.assertEqual(resolved.status_code, 200, resolved.text)
            self.assertEqual(resolved.json()["content"], "完了")

    def test_standalone_app_redirects_root_and_mounts_general(self) -> None:
        standalone = create_standalone_app(general_app=self.app)
        with TestClient(standalone) as client:
            root = client.get("/", follow_redirects=False)
            self.assertEqual(root.status_code, 307)
            self.assertEqual(root.headers["location"], "/general/")
            self.assertEqual(client.get("/general/api/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
