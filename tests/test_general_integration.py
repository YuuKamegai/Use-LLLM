from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from use_lllm.core.endpoints import EndpointRegistry
from use_lllm.core.mcp_registry import MCPRegistry
from use_lllm.core.ollama import OllamaResponse
from use_lllm.core.sessions import SessionStore
from use_lllm.core.settings_store import (
    MCPServerSpec,
    Settings,
    default_settings,
    load_settings,
    save_settings,
)
from use_lllm.general.agent_loop import GeneralAgentLoop


@dataclass
class FakeTool:
    name: str
    inputSchema: dict
    title: str | None = None
    description: str | None = "read a value"
    annotations: dict | None = None


class FakeBlock:
    def model_dump(self, **_kwargs):
        return {"type": "text", "text": "tool-observation"}


class FakeSession:
    async def initialize(self):
        return SimpleNamespace(
            serverInfo=SimpleNamespace(name="fake", version="1"),
            protocolVersion="2025-06-18",
        )

    async def list_tools(self, cursor=None):
        return SimpleNamespace(
            tools=[
                FakeTool(
                    "peek",
                    {"type": "object", "additionalProperties": False},
                    annotations={"readOnlyHint": True},
                )
            ],
            nextCursor=None,
        )

    async def call_tool(self, name, arguments, *, session_id=None):
        return SimpleNamespace(isError=False, content=[FakeBlock()], structuredContent=None)


class FakeOllama:
    def __init__(self) -> None:
        self.responses = [
            OllamaResponse(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "other::peek", "arguments": {}}}],
                },
                "fake",
                None,
                None,
                None,
            ),
            OllamaResponse(
                {"role": "assistant", "content": "観測結果を確認しました。"},
                "fake",
                None,
                None,
                None,
            ),
        ]

    async def chat(self, messages, **kwargs):
        return self.responses.pop(0)


class GeneralFoundationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_registry_agent_and_session_compose(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            defaults = default_settings()
            settings = Settings(
                defaults.endpoints,
                defaults.selected_endpoint,
                (
                    MCPServerSpec(
                        "other",
                        "python",
                        args=("server.py",),
                        read_only_auto=True,
                    ),
                ),
            )
            path = root / "settings.json"
            save_settings(path, settings)
            loaded = load_settings(path)
            endpoint = EndpointRegistry(list(loaded.endpoints), loaded.selected_endpoint).selected()
            endpoint.to_ollama_config().validate()

            @asynccontextmanager
            async def session_factory(_spec):
                yield FakeSession()

            registry = MCPRegistry(loaded.mcp_servers, session_factory=session_factory)
            await registry.connect("other")
            store = SessionStore(root / "state")
            session = store.create_session("integration", surface="general")
            result = await GeneralAgentLoop(FakeOllama(), store, registry).chat(
                session["id"], "確認してください"
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(registry.tool_names(), ["other::peek"])
            restored = SessionStore(root / "state").get_session(session["id"])
            self.assertEqual(restored["surface"], "general")
            self.assertEqual(
                [item["role"] for item in restored["messages"]],
                ["user", "assistant", "tool", "assistant"],
            )
            self.assertEqual(restored["messages"][-1]["content"], "観測結果を確認しました。")


if __name__ == "__main__":
    unittest.main()
