from __future__ import annotations

from dataclasses import dataclass
import unittest

from pathlib import Path

from use_lllm.core.config import MCPServerConfig
from use_lllm.core.mcp_client import MCPClient, MCPConnectionError, list_all_tools


@dataclass
class FakeTool:
    name: str
    title: str | None = None
    description: str | None = None
    inputSchema: dict | None = None

    def __post_init__(self) -> None:
        if self.inputSchema is None:
            self.inputSchema = {"type": "object"}


@dataclass
class FakePage:
    tools: list[FakeTool]
    nextCursor: str | None = None


class FakeSession:
    def __init__(self, pages: dict[str | None, FakePage]) -> None:
        self.pages = pages
        self.cursors: list[str | None] = []

    async def list_tools(self, cursor: str | None = None) -> FakePage:
        self.cursors.append(cursor)
        return self.pages[cursor]


class ToolDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_all_tools_follows_pagination(self) -> None:
        session = FakeSession(
            {
                None: FakePage([FakeTool("arf2_parser")], "next"),
                "next": FakePage([FakeTool("arf_parser")]),
            }
        )

        tools = await list_all_tools(session)

        self.assertEqual([tool.name for tool in tools], ["arf2_parser", "arf_parser"])
        self.assertEqual(session.cursors, [None, "next"])

    async def test_list_all_tools_rejects_repeated_cursor(self) -> None:
        session = FakeSession(
            {
                None: FakePage([], "repeat"),
                "repeat": FakePage([], "repeat"),
            }
        )

        with self.assertRaisesRegex(MCPConnectionError, "同じカーソル"):
            await list_all_tools(session)

    async def test_read_only_smoke_rejects_non_allowlisted_tool(self) -> None:
        client = MCPClient(
            MCPServerConfig(command=Path(__file__), server_script=Path(__file__))
        )

        with self.assertRaisesRegex(MCPConnectionError, "許可されていない"):
            await client.call_read_only_smoke_tool("write_report")


if __name__ == "__main__":
    unittest.main()
