"""Compose trusted built-in Web tools with an ordinary MCP registry."""

from __future__ import annotations

from use_lllm.core.mcp_client import MCPClient, MCPConnectionError, ToolDescription
from use_lllm.core.mcp_registry import MCPRegistry, RegisteredTool
from use_lllm.core.policy import ToolDecision, ToolSafety

from .contracts import to_mcp_result


class WebRegistry:
    def __init__(self, delegate, *, search, pages, enabled: bool):
        self.delegate = delegate
        self.search = search
        self.pages = pages
        self.enabled = enabled
        if enabled and "web" in delegate.server_names():
            raise ValueError("MCPサーバー名webは内蔵Web検索と重複しています。")
        annotations = {"readOnlyHint": True, "openWorldHint": True}
        self._builtin = (
            [
                RegisteredTool(
                    "web",
                    ToolDescription(
                        "search",
                        "Web検索",
                        "一般Webを検索し、タイトル・URL・概要を返す。必要な根拠本文はread_pageで確認する。",
                        {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                                "max_results": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 10,
                                    "default": 5,
                                },
                            },
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                        annotations,
                    ),
                ),
                RegisteredTool(
                    "web",
                    ToolDescription(
                        "read_page",
                        "ページ本文取得",
                        "公開URLの本文を読み、事実確認の根拠を取得する。URL・日時・省略の有無も返す。",
                        {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "minLength": 1, "maxLength": 2048}
                            },
                            "required": ["url"],
                            "additionalProperties": False,
                        },
                        annotations,
                    ),
                ),
            ]
            if enabled
            else []
        )

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def tools(self):
        return self.delegate.tools() + self._builtin

    def tool_names(self):
        return [item.qualified_name for item in self.tools()]

    def ollama_tools(self, query=None, *, limit=None, excluded=None):
        # Reuse the existing ranking/limit rules against the combined tools().
        return MCPRegistry.ollama_tools(self, query, limit=limit, excluded=excluded)

    def get_tool(self, name):
        matches = [
            item
            for item in self.tools()
            if item.qualified_name == name or ("::" not in name and item.description.name == name)
        ]
        if len(matches) != 1:
            raise MCPConnectionError(f"利用できない、または曖昧なツール名です: {name}")
        return matches[0]

    def builtin_name(self, name):
        try:
            tool = self.get_tool(name)
        except MCPConnectionError:
            return None
        return tool.qualified_name if any(tool is item for item in self._builtin) else None

    def server_instructions(self):
        return self.delegate.server_instructions()

    def decide(self, name, *, approved=False, network_mode="offline"):
        tool = self.get_tool(name)
        if any(tool is item for item in self._builtin):
            return ToolDecision(
                tool.qualified_name,
                ToolSafety.EXTERNAL_NETWORK,
                True,
                False,
                "Web検索専用の有効化設定により許可されています。",
            )
        return self.delegate.decide(
            tool.qualified_name, approved=approved, network_mode=network_mode
        )

    async def call_tool(
        self, name, arguments=None, *, approved=False, network_mode="offline", session_id=None
    ):
        tool = self.get_tool(name)
        if any(tool is item for item in self._builtin):
            values = arguments or {}
            MCPClient._validate_arguments(tool.description, values)
            if tool.description.name == "search":
                result = await self.search.search(**values)
            else:
                result = await self.pages.read_page(**values)
            return to_mcp_result(tool.qualified_name, result)
        return await self.delegate.call_tool(
            tool.qualified_name,
            arguments,
            approved=approved,
            network_mode=network_mode,
            session_id=session_id,
        )

    def is_replay_safe(self, name):
        return True if self.builtin_name(name) else self.delegate.is_replay_safe(name)

    def may_write_files(self, name):
        return False if self.builtin_name(name) else self.delegate.may_write_files(name)

    def connection_generation(self, session_id, server_name):
        if self.enabled and server_name == "web":
            return 1
        return self.delegate.connection_generation(session_id, server_name)

    async def ensure_session(self, session_id, server_name):
        if self.enabled and server_name == "web":
            return 1
        return await self.delegate.ensure_session(session_id, server_name)

    async def close_all(self):
        await self.delegate.close_all()
