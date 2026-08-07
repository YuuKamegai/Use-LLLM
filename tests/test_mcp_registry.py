from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from use_lllm.core.mcp_client import MCPConnectionError
from use_lllm.core.mcp_registry import MCPRegistry, _oauth_provider
from use_lllm.core.policy import ToolPolicyError
from use_lllm.core.settings_store import MCPServerSpec


@dataclass
class FakeTool:
    name: str
    inputSchema: dict
    title: str | None = None
    description: str | None = None
    annotations: dict | None = None


@dataclass
class FakePage:
    tools: list[FakeTool]
    nextCursor: str | None = None


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text

    def model_dump(self, **_kwargs):
        return {"type": "text", "text": self.text}


class FakeSession:
    def __init__(self, tools: list[FakeTool]) -> None:
        self.tools = tools
        self.calls: list[tuple[str, dict]] = []

    async def initialize(self):
        return SimpleNamespace(
            serverInfo=SimpleNamespace(name="fake", version="1.0"),
            protocolVersion="2025-06-18",
        )

    async def list_tools(self, cursor=None):
        return FakePage(self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            isError=False,
            content=[FakeBlock(f"called:{name}")],
            structuredContent={"arguments": arguments},
        )


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sessions: dict[str, FakeSession] = {
            "ms-data-parser": FakeSession(
                [
                    FakeTool(
                        "arf_parser",
                        {
                            "type": "object",
                            "properties": {"input": {"type": "string"}},
                            "required": ["input"],
                        },
                        annotations={"readOnlyHint": True},
                    )
                ]
            ),
            "other": FakeSession(
                [
                    FakeTool(
                        "peek",
                        {"type": "object", "additionalProperties": False},
                        annotations={"readOnlyHint": True},
                    ),
                    FakeTool("danger", {"type": "object"}),
                ]
            ),
        }

        @asynccontextmanager
        async def factory(spec):
            yield self.sessions[spec.name]

        self.factory = factory

    async def test_connect_namespaces_tools_and_exposes_ollama_schema(self) -> None:
        registry = MCPRegistry([MCPServerSpec("other", "python")], session_factory=self.factory)
        status = await registry.connect("other")

        self.assertEqual(status["status"], "connected")
        self.assertEqual(registry.tool_names(), ["other::peek", "other::danger"])
        schema = registry.ollama_tools()[0]
        self.assertEqual(schema["function"]["name"], "other::peek")
        self.assertEqual(schema["function"]["parameters"]["type"], "object")

        routed = registry.ollama_tools("dangerを実行", limit=1)
        self.assertEqual(routed[0]["function"]["name"], "other::danger")

    async def test_read_only_auto_validates_and_calls(self) -> None:
        registry = MCPRegistry(
            [MCPServerSpec("ms-data-parser", "python", read_only_auto=True)],
            session_factory=self.factory,
        )
        await registry.connect("ms-data-parser")
        result = await registry.call_tool(
            "ms-data-parser::arf_parser", {"input": "C:/data/test.arf"}
        )

        self.assertFalse(result.is_error)
        self.assertEqual(
            self.sessions["ms-data-parser"].calls,
            [("arf_parser", {"input": "C:/data/test.arf"})],
        )

    async def test_session_scoped_connections_preserve_and_isolate_server_state(self) -> None:
        tools = [
            FakeTool("arf_parser", {"type": "object"}, annotations={"readOnlyHint": True}),
            FakeTool(
                "arf_pca_preprocessed", {"type": "object"}, annotations={"readOnlyHint": True}
            ),
        ]
        opened = []
        closed = []

        class StatefulSession(FakeSession):
            def __init__(self):
                super().__init__(tools)
                self.loaded = False

            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                if name == "arf_parser":
                    self.loaded = True
                    text = "loaded"
                else:
                    text = "pca" if self.loaded else "missing"
                return SimpleNamespace(
                    isError=False,
                    content=[FakeBlock(text)],
                    structuredContent=None,
                )

        @asynccontextmanager
        async def factory(_spec):
            session = StatefulSession()
            opened.append(session)
            try:
                yield session
            finally:
                closed.append(session)

        registry = MCPRegistry(
            [MCPServerSpec("ms-data-parser", "python", read_only_auto=True)],
            session_factory=factory,
        )
        await registry.connect("ms-data-parser")
        await asyncio.create_task(
            registry.call_tool("ms-data-parser::arf_parser", {}, session_id="session-a")
        )
        same_session = await asyncio.create_task(
            registry.call_tool("ms-data-parser::arf_pca_preprocessed", {}, session_id="session-a")
        )
        other_session = await asyncio.create_task(
            registry.call_tool("ms-data-parser::arf_pca_preprocessed", {}, session_id="session-b")
        )

        self.assertEqual(same_session.text, "pca")
        self.assertEqual(other_session.text, "missing")
        self.assertEqual(len(opened), 3)  # discovery + one process per chat session
        await asyncio.create_task(registry.close_session("session-a"))
        self.assertIn(opened[1], closed)
        self.assertNotIn(opened[2], closed)
        await registry.close_all()

    async def test_unknown_requires_approval_then_calls(self) -> None:
        registry = MCPRegistry([MCPServerSpec("other", "python")], session_factory=self.factory)
        await registry.connect("other")

        with self.assertRaises(ToolPolicyError):
            await registry.call_tool("other::danger", {})
        result = await registry.call_tool("other::danger", {}, approved=True)
        self.assertEqual(result.text, "called:danger")

    async def test_annotated_read_only_requires_server_opt_in(self) -> None:
        registry = MCPRegistry([MCPServerSpec("other", "python")], session_factory=self.factory)
        await registry.connect("other")
        with self.assertRaises(ToolPolicyError):
            await registry.call_tool("other::peek", {})
        result = await registry.call_tool("other::peek", {}, approved=True)
        self.assertEqual(result.text, "called:peek")

    async def test_invalid_arguments_and_disconnected_calls_are_rejected(self) -> None:
        registry = MCPRegistry(
            [MCPServerSpec("ms-data-parser", "python", read_only_auto=True)],
            session_factory=self.factory,
        )
        with self.assertRaisesRegex(MCPConnectionError, "接続"):
            await registry.call_tool("ms-data-parser::arf_parser", {"input": "x"})
        await registry.connect("ms-data-parser")
        with self.assertRaisesRegex(MCPConnectionError, "スキーマ"):
            await registry.call_tool("ms-data-parser::arf_parser", {})
        await registry.disconnect("ms-data-parser")
        self.assertEqual(registry.tool_names(), [])

    def test_duplicate_server_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "重複"):
            MCPRegistry(
                [MCPServerSpec("same", "a"), MCPServerSpec("same", "b")],
                session_factory=self.factory,
            )

    def test_oauth_redirect_uses_runtime_webui_port(self) -> None:
        spec = MCPServerSpec(
            "remote",
            transport="streamable_http",
            url="https://example.invalid/mcp",
            auth_mode="oauth",
        )
        with patch.dict("os.environ", {"USE_LLLM_WEB_PORT": "54321"}):
            provider = _oauth_provider(spec)

        self.assertIsNotNone(provider)
        redirect = str(provider.context.client_metadata.redirect_uris[0])
        self.assertEqual(
            redirect,
            "http://127.0.0.1:54321/general/api/mcp-oauth/callback",
        )


if __name__ == "__main__":
    unittest.main()
