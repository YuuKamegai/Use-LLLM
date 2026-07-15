"""Protocol-level client for the local ms-data-parser MCP server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from use_lllm.core.config import MCPServerConfig
from use_lllm.core.policy import enforce_tool


class MCPConnectionError(RuntimeError):
    """Raised when initialize or tool discovery cannot complete."""


READ_ONLY_SMOKE_TOOLS = frozenset({"list_reports", "list_data_files"})


@dataclass(frozen=True, slots=True)
class ToolDescription:
    name: str
    title: str | None
    description: str | None
    input_schema: dict[str, Any]
    annotations: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_schema,
            "annotations": self.annotations,
        }


@dataclass(frozen=True, slots=True)
class MCPServerSnapshot:
    server_name: str
    server_version: str
    protocol_version: str
    tools: tuple[ToolDescription, ...]

    @property
    def tool_count(self) -> int:
        return len(self.tools)

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": {
                "name": self.server_name,
                "version": self.server_version,
                "protocol_version": self.protocol_version,
            },
            "tool_count": self.tool_count,
            "tools": [tool.to_dict() for tool in self.tools],
        }


@dataclass(frozen=True, slots=True)
class MCPToolResult:
    tool_name: str
    is_error: bool
    content: tuple[dict[str, Any], ...]
    structured_content: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "is_error": self.is_error,
            "content": list(self.content),
            "structured_content": self.structured_content,
        }

    @property
    def text(self) -> str:
        return "\n".join(
            str(block.get("text", "")) for block in self.content if block.get("type") == "text"
        )


@dataclass(frozen=True, slots=True)
class MCPToolCall:
    name: str
    arguments: dict[str, Any]
    approved: bool = False


class ToolListingSession(Protocol):
    async def list_tools(self, cursor: str | None = None) -> Any: ...


async def list_all_tools(session: ToolListingSession) -> tuple[ToolDescription, ...]:
    """Read every tools/list page instead of assuming a fixed tool set."""

    cursor: str | None = None
    tools: list[ToolDescription] = []
    seen_cursors: set[str] = set()
    while True:
        page = await session.list_tools(cursor=cursor)
        tools.extend(
            ToolDescription(
                name=tool.name,
                title=tool.title,
                description=tool.description,
                input_schema=tool.inputSchema,
                annotations=(
                    tool.annotations
                    if isinstance(getattr(tool, "annotations", None), dict)
                    else tool.annotations.model_dump(mode="json", by_alias=True)
                    if getattr(tool, "annotations", None) is not None
                    else None
                ),
            )
            for tool in page.tools
        )
        cursor = page.nextCursor
        if cursor is None:
            break
        if cursor in seen_cursors:
            raise MCPConnectionError(f"tools/list が同じカーソルを繰り返しました: {cursor}")
        seen_cursors.add(cursor)
    return tuple(tools)


class MCPClient:
    """Owns the stdio subprocess and exposes a small protocol boundary."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[ClientSession]:
        self._config.validate()
        parameters = StdioServerParameters(
            command=str(self._config.command),
            args=[str(self._config.server_script)],
            cwd=self._config.working_directory,
        )
        try:
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    yield session
        except MCPConnectionError:
            raise
        except Exception as exc:
            raise MCPConnectionError("ms-data-parser MCPとのstdio通信に失敗しました") from exc

    async def inspect_server(self) -> MCPServerSnapshot:
        """Initialize the server and dynamically retrieve its complete tool set."""

        try:
            async with asyncio.timeout(self._config.startup_timeout_seconds):
                async with self.connect() as session:
                    initialized = await session.initialize()
                    tools = await list_all_tools(session)
        except TimeoutError as exc:
            raise MCPConnectionError("ms-data-parser MCPの初期化がタイムアウトしました") from exc

        return MCPServerSnapshot(
            server_name=initialized.serverInfo.name,
            server_version=initialized.serverInfo.version,
            protocol_version=str(initialized.protocolVersion),
            tools=tools,
        )

    async def call_read_only_smoke_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> MCPToolResult:
        """Call one explicitly allowlisted, non-mutating diagnostic tool."""

        if tool_name not in READ_ONLY_SMOKE_TOOLS:
            allowed = ", ".join(sorted(READ_ONLY_SMOKE_TOOLS))
            raise MCPConnectionError(
                f"read-onlyスモークテストで許可されていないツールです: "
                f"{tool_name} (許可: {allowed})"
            )
        try:
            async with asyncio.timeout(self._config.startup_timeout_seconds):
                async with self.connect() as session:
                    await session.initialize()
                    advertised = {tool.name for tool in await list_all_tools(session)}
                    if tool_name not in advertised:
                        raise MCPConnectionError(f"MCPがツールを公開していません: {tool_name}")
                    result = await session.call_tool(tool_name, arguments or {})
        except TimeoutError as exc:
            raise MCPConnectionError(
                f"MCPツール {tool_name} の呼び出しがタイムアウトしました"
            ) from exc

        content = tuple(block.model_dump(mode="json", by_alias=True) for block in result.content)
        return MCPToolResult(
            tool_name=tool_name,
            is_error=result.isError,
            content=content,
            structured_content=result.structuredContent,
        )

    @staticmethod
    def _serialize_result(tool_name: str, result: Any) -> MCPToolResult:
        return MCPToolResult(
            tool_name=tool_name,
            is_error=bool(result.isError),
            content=tuple(block.model_dump(mode="json", by_alias=True) for block in result.content),
            structured_content=result.structuredContent,
        )

    @staticmethod
    def _validate_arguments(tool: ToolDescription, arguments: dict[str, Any]) -> None:
        try:
            Draft202012Validator(tool.input_schema).validate(arguments)
        except ValidationError as exc:
            location = ".".join(str(item) for item in exc.absolute_path) or "<root>"
            raise MCPConnectionError(
                f"{tool.name} の引数がスキーマに適合しません ({location}): {exc.message}"
            ) from exc

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        approved: bool = False,
        network_mode: str = "offline",
        timeout_seconds: float | None = None,
    ) -> MCPToolResult:
        """Validate, authorize and call one advertised MCP tool."""

        enforce_tool(tool_name, approved=approved, network_mode=network_mode)
        timeout = timeout_seconds or max(self._config.startup_timeout_seconds, 300.0)
        try:
            async with asyncio.timeout(timeout):
                async with self.connect() as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in await list_all_tools(session)}
                    if tool_name not in tools:
                        raise MCPConnectionError(f"MCPがツールを公開していません: {tool_name}")
                    values = arguments or {}
                    self._validate_arguments(tools[tool_name], values)
                    result = await session.call_tool(tool_name, values)
                    return self._serialize_result(tool_name, result)
        except TimeoutError as exc:
            raise MCPConnectionError(f"MCPツール {tool_name} がタイムアウトしました。") from exc

    async def call_sequence(
        self,
        calls: list[MCPToolCall],
        *,
        network_mode: str = "offline",
        timeout_seconds: float = 900.0,
    ) -> tuple[MCPToolResult, ...]:
        """Execute a stateful tool sequence in one MCP session."""

        for call in calls:
            enforce_tool(call.name, approved=call.approved, network_mode=network_mode)
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.connect() as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in await list_all_tools(session)}
                    results: list[MCPToolResult] = []
                    for call in calls:
                        if call.name not in tools:
                            raise MCPConnectionError(f"MCPがツールを公開していません: {call.name}")
                        self._validate_arguments(tools[call.name], call.arguments)
                        raw = await session.call_tool(call.name, call.arguments)
                        result = self._serialize_result(call.name, raw)
                        results.append(result)
                        if result.is_error:
                            break
                    return tuple(results)
        except TimeoutError as exc:
            raise MCPConnectionError("MCPツール列がタイムアウトしました。") from exc
